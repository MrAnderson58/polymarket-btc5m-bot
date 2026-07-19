"""S41 — 30-minute news aggregation into market_news_summary."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from bot.research.market_events.db import execute_with_retry, market_events_connection
from bot.research.market_events.signal_intelligence.news_intelligence.tagging import (
    score_importance,
    score_sentiment,
    tag_news_item,
)
from bot.research.market_events.signal_intelligence.news_intelligence.watchlist import (
    detect_symbols,
    watched_symbols,
)

logger = logging.getLogger(__name__)

AGGREGATION_WINDOW_SEC = 30 * 60
DEDUP_RATIO = 0.90


def _row_to_tagged(row: Any) -> dict[str, Any]:
    title = str(row["title"] or "")
    body = str(row["summary"] or "")
    raw_syms = str(row["symbols"] or "")
    parsed = [s.strip().upper() for s in raw_syms.split(",") if s.strip()]
    if not parsed:
        parsed = detect_symbols(f"{title}\n{body}")
    return tag_news_item(
        timestamp=int(row["published_at"] or row["created_at"] or time.time()),
        source=str(row["source"] or "unknown"),
        title=title,
        body=body,
        url=str(row["url"] or ""),
        symbols=parsed,
    )


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    titles: list[str] = []
    for item in sorted(items, key=lambda x: int(x.get("timestamp") or 0), reverse=True):
        title = (item.get("title") or "").strip().lower()
        if not title:
            continue
        dup = False
        for prev in titles:
            if SequenceMatcher(None, title, prev).ratio() >= DEDUP_RATIO:
                dup = True
                break
        if dup:
            continue
        kept.append(item)
        titles.append(title)
    return kept


def _cluster_key(item: dict[str, Any]) -> str:
    syms = item.get("symbols") or []
    if syms:
        return ",".join(sorted(syms)[:3])
    title = (item.get("title") or "").lower()
    tokens = [t for t in re_tokens(title) if len(t) > 3][:4]
    return " ".join(tokens) or "general"


def re_tokens(text: str) -> list[str]:
    import re
    return re.findall(r"[a-z0-9]+", text.lower())


def _summarize_cluster(items: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    titles = [str(i.get("title") or "") for i in items[:8]]
    bodies = [str(i.get("body") or "")[:200] for i in items[:5]]
    blob_title = " | ".join(titles)
    blob_body = " ".join(bodies)
    bull, bear, neu = score_sentiment(blob_title, blob_body)
    importance = max((float(i.get("importance") or 0) for i in items), default=0.3)
    importance = max(importance, score_importance(blob_title, blob_body))
    sources = sorted({str(i.get("source") or "") for i in items if i.get("source")})
    headline = titles[0] if titles else f"{symbol} news update"
    summary_lines = [
        f"{symbol}: {headline}",
        f"Headlines in window: {len(items)}.",
    ]
    if len(titles) > 1:
        summary_lines.append("Related: " + "; ".join(titles[1:4]))
    return {
        "symbol": symbol,
        "summary": "\n".join(summary_lines)[:2000],
        "bullish_score": bull,
        "bearish_score": bear,
        "neutral_score": neu,
        "importance": round(importance, 3),
        "sources": sources,
        "headline_count": len(items),
        "cluster_titles": titles[:10],
    }


def load_recent_news(conn: Any, *, since_ts: int, limit: int = 500) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, published_at, source, title, summary, url, symbols, category, created_at
        FROM market_news_feed_n11
        WHERE published_at >= ? OR created_at >= ?
        ORDER BY published_at DESC, id DESC
        LIMIT ?
        """,
        (since_ts, since_ts, limit),
    ).fetchall()
    return [_row_to_tagged(r) for r in rows]


def save_news_summaries(
    conn: Any,
    *,
    period_start: int,
    period_end: int,
    rows: list[dict[str, Any]],
) -> int:
    written = 0
    for row in rows:
        execute_with_retry(
            conn,
            """
            INSERT INTO market_news_summary (
              period_start, period_end, symbol, summary,
              bullish_score, bearish_score, neutral_score,
              importance, sources, headline_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                period_start,
                period_end,
                row["symbol"],
                row["summary"],
                float(row["bullish_score"]),
                float(row["bearish_score"]),
                float(row["neutral_score"]),
                float(row["importance"]),
                json.dumps(row.get("sources") or [], ensure_ascii=False),
                int(row["headline_count"]),
                int(time.time()),
            ),
        )
        written += 1
    return written


def run_news_aggregation_cycle_s41(
    *,
    window_sec: int = AGGREGATION_WINDOW_SEC,
    now: int | None = None,
) -> dict[str, Any]:
    """Collect → dedupe → cluster → sentiment → persist market_news_summary."""
    now_ts = int(now if now is not None else time.time())
    period_end = now_ts
    period_start = now_ts - int(window_sec)

    with market_events_connection() as conn:
        raw = load_recent_news(conn, since_ts=period_start)
        items = _dedupe(raw)

        by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        watch = set(watched_symbols())
        for item in items:
            syms = [s for s in (item.get("symbols") or []) if s in watch] or (
                [s for s in (item.get("symbols") or [])][:1]
            )
            if not syms:
                by_symbol["MACRO"].append(item)
                continue
            for sym in syms:
                by_symbol[sym].append(item)

        # topic clusters within symbol via shared cluster key
        summaries: list[dict[str, Any]] = []
        for sym, group in by_symbol.items():
            clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for it in group:
                clusters[_cluster_key(it)].append(it)
            # merge small clusters into one symbol summary
            flat = [it for bucket in clusters.values() for it in bucket]
            if not flat:
                continue
            summaries.append(_summarize_cluster(flat, sym))

        n = save_news_summaries(
            conn,
            period_start=period_start,
            period_end=period_end,
            rows=summaries,
        )
        conn.commit()

    result = {
        "period_start": period_start,
        "period_end": period_end,
        "fetched": len(raw),
        "deduped": len(items),
        "summaries_written": n,
        "symbols": sorted({r["symbol"] for r in summaries}),
    }
    logger.info(
        "s41 aggregation fetched=%s deduped=%s summaries=%s",
        result["fetched"],
        result["deduped"],
        result["summaries_written"],
    )
    return result
