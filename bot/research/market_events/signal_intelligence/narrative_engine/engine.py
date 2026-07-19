"""S42 core cycle — build per-asset intelligence + top movers + reports."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any

from bot.research.market_events.db import execute_with_retry, market_events_connection
from bot.research.market_events.signal_intelligence.narrative_engine.confidence import (
    agreement_from_sentiments,
    calculate_confidence,
    freshness_sec_from_timestamps,
)
from bot.research.market_events.signal_intelligence.narrative_engine.narratives import (
    detect_narratives,
    narratives_to_text,
)
from bot.research.market_events.signal_intelligence.narrative_engine.scoring import (
    importance_score,
    risk_level_from_scores,
    sentiment_scores,
    theme_scores,
)
from bot.research.market_events.signal_intelligence.narrative_engine.watchlist import (
    detect_symbols,
    source_quality,
    watched_symbols,
)

logger = logging.getLogger(__name__)

WINDOW_SEC = 3600


def _table_exists(conn: Any, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _load_n11(conn: Any, *, since_ts: int, limit: int = 800) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, published_at, source, title, summary, url, symbols, created_at
        FROM market_news_feed_n11
        WHERE published_at >= ? OR created_at >= ?
        ORDER BY published_at DESC, id DESC
        LIMIT ?
        """,
        (since_ts, since_ts, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        title = str(r["title"] or "")
        body = str(r["summary"] or "")
        raw_syms = [
            s.strip().upper()
            for s in str(r["symbols"] or "").split(",")
            if s.strip()
        ]
        if not raw_syms:
            raw_syms = detect_symbols(f"{title}\n{body}")
        bull, bear, neu = sentiment_scores(title, body)
        imp = importance_score(title, body)
        themes = theme_scores(title, body)
        out.append({
            "id": r["id"],
            "timestamp": int(r["published_at"] or r["created_at"] or 0),
            "source": str(r["source"] or "unknown"),
            "title": title,
            "body": body,
            "url": str(r["url"] or ""),
            "symbols": raw_syms,
            "bullish": bull,
            "bearish": bear,
            "neutral": neu,
            "importance": imp,
            "narratives": detect_narratives(f"{title}\n{body}"),
            **themes,
        })
    return out


def _load_summaries(conn: Any, *, since_ts: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "market_news_summary"):
        return []
    rows = conn.execute(
        """
        SELECT symbol, summary, bullish_score, bearish_score, neutral_score,
               importance, sources, headline_count, period_end
        FROM market_news_summary
        WHERE period_end >= ?
        ORDER BY importance DESC
        LIMIT 200
        """,
        (since_ts,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            sources = json.loads(r["sources"] or "[]")
        except Exception:
            sources = []
        out.append({
            "symbol": str(r["symbol"] or "").upper(),
            "summary": str(r["summary"] or ""),
            "bullish": float(r["bullish_score"] or 0),
            "bearish": float(r["bearish_score"] or 0),
            "neutral": float(r["neutral_score"] or 0),
            "importance": float(r["importance"] or 0),
            "sources": sources if isinstance(sources, list) else [],
            "headline_count": int(r["headline_count"] or 0),
            "timestamp": int(r["period_end"] or 0),
        })
    return out


def _load_briefs(conn: Any, *, since_ts: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "market_daily_briefs"):
        return []
    rows = conn.execute(
        """
        SELECT global_narrative, top_bullish_json, top_bearish_json,
               macro_events_json, fed_json, whales_json, polymarket_json,
               risk_level, created_at
        FROM market_daily_briefs
        WHERE created_at >= ?
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (since_ts,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_asset_record(
    symbol: str,
    items: list[dict[str, Any]],
    *,
    summary_hint: dict[str, Any] | None,
    now: int,
) -> dict[str, Any]:
    if not items and summary_hint:
        items = [{
            "title": (summary_hint.get("summary") or "").split("\n")[0],
            "body": summary_hint.get("summary") or "",
            "source": (summary_hint.get("sources") or ["summary"])[0]
            if summary_hint.get("sources") else "summary",
            "timestamp": int(summary_hint.get("timestamp") or now),
            "bullish": float(summary_hint.get("bullish") or 0.25),
            "bearish": float(summary_hint.get("bearish") or 0.25),
            "neutral": float(summary_hint.get("neutral") or 0.5),
            "importance": float(summary_hint.get("importance") or 0.3),
            "narratives": detect_narratives(summary_hint.get("summary") or ""),
            "macro_score": 0.2,
            "whale_score": 0.15,
            "polymarket_score": 0.1,
        }]

    news_count = len(items)
    if news_count == 0:
        return {
            "symbol": symbol,
            "news_count": 0,
            "bullish_score": 0.25,
            "bearish_score": 0.25,
            "neutral_score": 0.5,
            "importance": 0.2,
            "top_headlines": [],
            "summary": f"{symbol}: no material headlines in the last hour.",
            "narrative": "General",
            "risk_level": "LOW",
            "confidence": 0.15,
            "macro_score": 0.1,
            "whale_score": 0.1,
            "polymarket_score": 0.1,
            "market_score": 0.2,
            "created_at": now,
        }

    bull = sum(float(i["bullish"]) for i in items) / news_count
    bear = sum(float(i["bearish"]) for i in items) / news_count
    neu = sum(float(i["neutral"]) for i in items) / news_count
    importance = max(float(i["importance"]) for i in items)
    macro = sum(float(i.get("macro_score") or 0) for i in items) / news_count
    whale = sum(float(i.get("whale_score") or 0) for i in items) / news_count
    poly = sum(float(i.get("polymarket_score") or 0) for i in items) / news_count

    labels: list[str] = []
    for i in items:
        for lab in i.get("narratives") or []:
            if lab not in labels:
                labels.append(lab)

    headlines = [
        {
            "title": i["title"][:200],
            "source": i.get("source"),
            "importance": i.get("importance"),
        }
        for i in sorted(items, key=lambda x: float(x.get("importance") or 0), reverse=True)[:5]
    ]
    top_titles = [h["title"] for h in headlines]
    narrative = narratives_to_text(labels)
    summary = (
        f"{symbol}: {news_count} headlines. Narrative: {narrative}. "
        f"Lead: {top_titles[0] if top_titles else '—'}"
    )
    if summary_hint and summary_hint.get("summary"):
        summary = f"{summary}\nCluster: {str(summary_hint['summary']).split(chr(10))[0][:240]}"

    quals = [source_quality(str(i.get("source") or "")) for i in items]
    conf = calculate_confidence(
        source_qualities=quals,
        headline_count=news_count,
        importance=importance,
        agreement=agreement_from_sentiments(items),
        freshness_sec=freshness_sec_from_timestamps(
            [int(i.get("timestamp") or 0) for i in items],
            now=now,
        ),
        now=now,
    )
    risk = risk_level_from_scores(
        bearish=bear, importance=importance, narratives=labels,
    )
    market_score = round(
        min(1.0, 0.35 * bull + 0.25 * importance + 0.2 * conf + 0.2 * (1.0 - bear)),
        3,
    )
    return {
        "symbol": symbol,
        "news_count": news_count,
        "bullish_score": round(bull, 3),
        "bearish_score": round(bear, 3),
        "neutral_score": round(neu, 3),
        "importance": round(importance, 3),
        "top_headlines": headlines,
        "summary": summary[:2000],
        "narrative": narrative[:500],
        "risk_level": risk,
        "confidence": conf,
        "macro_score": round(macro, 3),
        "whale_score": round(whale, 3),
        "polymarket_score": round(poly, 3),
        "market_score": market_score,
        "created_at": now,
    }


def _save_asset_intel(conn: Any, rows: list[dict[str, Any]]) -> int:
    n = 0
    for row in rows:
        execute_with_retry(
            conn,
            """
            INSERT INTO market_asset_intelligence (
              created_at, symbol, news_count,
              bullish_score, bearish_score, neutral_score,
              importance, top_headlines_json, summary, narrative,
              risk_level, confidence,
              macro_score, whale_score, polymarket_score, market_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["created_at"]),
                row["symbol"],
                int(row["news_count"]),
                float(row["bullish_score"]),
                float(row["bearish_score"]),
                float(row["neutral_score"]),
                float(row["importance"]),
                json.dumps(row.get("top_headlines") or [], ensure_ascii=False),
                row["summary"],
                row["narrative"],
                row["risk_level"],
                float(row["confidence"]),
                float(row["macro_score"]),
                float(row["whale_score"]),
                float(row["polymarket_score"]),
                float(row["market_score"]),
            ),
        )
        n += 1
    return n


def _save_top_assets(conn: Any, *, now: int, assets: list[dict[str, Any]]) -> None:
    active = [a for a in assets if int(a.get("news_count") or 0) > 0]
    top_bullish = sorted(
        active, key=lambda a: float(a["bullish_score"]) * float(a["importance"]), reverse=True,
    )[:5]
    top_bearish = sorted(
        active, key=lambda a: float(a["bearish_score"]) * float(a["importance"]), reverse=True,
    )[:5]
    most_discussed = sorted(
        active, key=lambda a: int(a["news_count"]), reverse=True,
    )[:5]

    def _pack(rows: list[dict[str, Any]]) -> str:
        return json.dumps(
            [
                {
                    "symbol": r["symbol"],
                    "news_count": r["news_count"],
                    "bullish_score": r["bullish_score"],
                    "bearish_score": r["bearish_score"],
                    "narrative": r["narrative"],
                    "confidence": r["confidence"],
                    "summary": r["summary"][:240],
                }
                for r in rows
            ],
            ensure_ascii=False,
        )

    execute_with_retry(
        conn,
        """
        INSERT INTO market_top_assets (
          timestamp, top_bullish_json, top_bearish_json, most_discussed_json
        ) VALUES (?, ?, ?, ?)
        """,
        (now, _pack(top_bullish), _pack(top_bearish), _pack(most_discussed)),
    )


def run_narrative_engine_cycle_s42(
    *,
    window_sec: int = WINDOW_SEC,
    now: int | None = None,
    write_reports: bool = True,
) -> dict[str, Any]:
    """Hourly: per-asset intelligence + top movers + markdown context."""
    now_ts = int(now if now is not None else time.time())
    since = now_ts - int(window_sec)
    watch = watched_symbols()

    with market_events_connection() as conn:
        feed = _load_n11(conn, since_ts=since)
        summaries = _load_summaries(conn, since_ts=since)
        briefs = _load_briefs(conn, since_ts=since - window_sec)

        by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in feed:
            for sym in item.get("symbols") or []:
                if sym in watch:
                    by_sym[sym].append(item)

        summary_by_sym = {
            str(s["symbol"]).upper(): s
            for s in summaries
            if s.get("symbol")
        }

        assets: list[dict[str, Any]] = []
        for sym in watch:
            assets.append(
                _build_asset_record(
                    sym,
                    by_sym.get(sym) or [],
                    summary_hint=summary_by_sym.get(sym),
                    now=now_ts,
                )
            )

        written = _save_asset_intel(conn, assets)
        _save_top_assets(conn, now=now_ts, assets=assets)
        conn.commit()

    report_paths: dict[str, str] = {}
    if write_reports:
        from bot.research.market_events.signal_intelligence.narrative_engine.reports import (
            write_narrative_reports_s42,
        )
        report_paths = write_narrative_reports_s42(
            assets=assets,
            briefs=briefs,
            now=now_ts,
        )

    result = {
        "created_at": now_ts,
        "window_sec": window_sec,
        "feed_items": len(feed),
        "summaries_used": len(summaries),
        "assets_written": written,
        "active_assets": sum(1 for a in assets if a["news_count"] > 0),
        "reports": report_paths,
    }
    logger.info(
        "s42 narrative cycle assets=%s active=%s feed=%s",
        written,
        result["active_assets"],
        result["feed_items"],
    )
    return result
