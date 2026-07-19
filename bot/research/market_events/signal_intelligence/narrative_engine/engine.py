"""S42/S42.1 core cycle — consume Intelligence Layer summaries into asset intel."""

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

# Hourly cadence for the worker; lookbacks must cover news-intel (30m agg / 2h brief).
WINDOW_SEC = 3600
# Align with news-intel brief window so summaries remain visible between cycles.
SUMMARY_LOOKBACK_SEC = 2 * 3600
BRIEF_LOOKBACK_SEC = 2 * 3600
FEED_LOOKBACK_SEC = 2 * 3600


def _utc_now() -> int:
    """Unix UTC seconds (time.time is always UTC-based)."""
    return int(time.time())


def _table_exists(conn: Any, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _load_n11(conn: Any, *, since_ts: int, limit: int = 800) -> list[dict[str, Any]]:
    """Load N11 rows by published_at OR created_at (UTC unix)."""
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


def _load_summaries_raw(conn: Any, *, since_ts: int) -> list[dict[str, Any]]:
    """Load summaries where period_end OR created_at is within lookback (UTC)."""
    if not _table_exists(conn, "market_news_summary"):
        return []
    rows = conn.execute(
        """
        SELECT id, symbol, summary, bullish_score, bearish_score, neutral_score,
               importance, sources, headline_count, period_start, period_end,
               created_at
        FROM market_news_summary
        WHERE period_end >= ? OR created_at >= ?
        ORDER BY period_end DESC, importance DESC, id DESC
        LIMIT 500
        """,
        (since_ts, since_ts),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            sources = json.loads(r["sources"] or "[]")
        except Exception:
            sources = []
        out.append({
            "id": int(r["id"]),
            "symbol": str(r["symbol"] or "").upper(),
            "summary": str(r["summary"] or ""),
            "bullish": float(r["bullish_score"] or 0),
            "bearish": float(r["bearish_score"] or 0),
            "neutral": float(r["neutral_score"] or 0),
            "importance": float(r["importance"] or 0),
            "sources": sources if isinstance(sources, list) else [],
            "headline_count": int(r["headline_count"] or 0),
            "period_start": int(r["period_start"] or 0),
            "period_end": int(r["period_end"] or 0),
            "created_at": int(r["created_at"] or 0),
            "timestamp": int(r["period_end"] or r["created_at"] or 0),
        })
    return out


def _load_briefs(conn: Any, *, since_ts: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "market_daily_briefs"):
        return []
    rows = conn.execute(
        """
        SELECT global_narrative, top_bullish_json, top_bearish_json,
               macro_events_json, fed_json, whales_json, polymarket_json,
               risk_level, created_at, period_end
        FROM market_daily_briefs
        WHERE created_at >= ? OR period_end >= ?
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (since_ts, since_ts),
    ).fetchall()
    return [dict(r) for r in rows]


def _pick_latest_summary_per_symbol(
    summaries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Keep the newest (period_end) summary per symbol."""
    best: dict[str, dict[str, Any]] = {}
    for s in summaries:
        sym = str(s.get("symbol") or "").upper()
        if not sym:
            continue
        prev = best.get(sym)
        if prev is None or int(s.get("period_end") or 0) > int(prev.get("period_end") or 0):
            best[sym] = s
        elif int(s.get("period_end") or 0) == int(prev.get("period_end") or 0):
            if float(s.get("importance") or 0) > float(prev.get("importance") or 0):
                best[sym] = s
    return best


def _build_asset_record(
    symbol: str,
    items: list[dict[str, Any]],
    *,
    summary_hint: dict[str, Any] | None,
    now: int,
) -> dict[str, Any]:
    synthetic_from_summary = False
    if not items and summary_hint:
        synthetic_from_summary = True
        hc = max(1, int(summary_hint.get("headline_count") or 1))
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
            "_headline_count": hc,
        }]

    news_count = len(items)
    if synthetic_from_summary and items:
        news_count = max(news_count, int(items[0].get("_headline_count") or 1))

    if news_count == 0:
        return {
            "symbol": symbol,
            "news_count": 0,
            "bullish_score": 0.25,
            "bearish_score": 0.25,
            "neutral_score": 0.5,
            "importance": 0.2,
            "top_headlines": [],
            "summary": f"{symbol}: no material headlines in the lookback window.",
            "narrative": "General",
            "risk_level": "LOW",
            "confidence": 0.15,
            "macro_score": 0.1,
            "whale_score": 0.1,
            "polymarket_score": 0.1,
            "market_score": 0.2,
            "created_at": now,
            "from_summary": False,
        }

    n_for_avg = max(1, len(items))
    bull = sum(float(i["bullish"]) for i in items) / n_for_avg
    bear = sum(float(i["bearish"]) for i in items) / n_for_avg
    neu = sum(float(i["neutral"]) for i in items) / n_for_avg
    importance = max(float(i["importance"]) for i in items)
    macro = sum(float(i.get("macro_score") or 0) for i in items) / n_for_avg
    whale = sum(float(i.get("whale_score") or 0) for i in items) / n_for_avg
    poly = sum(float(i.get("polymarket_score") or 0) for i in items) / n_for_avg

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
        for i in sorted(
            items, key=lambda x: float(x.get("importance") or 0), reverse=True,
        )[:5]
    ]
    top_titles = [h["title"] for h in headlines]
    narrative = narratives_to_text(labels)
    summary = (
        f"{symbol}: {news_count} headlines. Narrative: {narrative}. "
        f"Lead: {top_titles[0] if top_titles else '—'}"
    )
    if summary_hint and summary_hint.get("summary"):
        summary = (
            f"{summary}\nCluster: "
            f"{str(summary_hint['summary']).split(chr(10))[0][:240]}"
        )

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
        "from_summary": synthetic_from_summary or bool(summary_hint),
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
        active,
        key=lambda a: float(a["bullish_score"]) * float(a["importance"]),
        reverse=True,
    )[:5]
    top_bearish = sorted(
        active,
        key=lambda a: float(a["bearish_score"]) * float(a["importance"]),
        reverse=True,
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


def format_narrative_debug_s42(debug: dict[str, Any]) -> str:
    """Human-readable --debug dump for ops."""
    lines: list[str] = []
    lines.append(f"now_utc: {debug.get('now_utc')}")
    lines.append(
        f"lookback: feed={debug.get('feed_lookback_sec')}s "
        f"summary={debug.get('summary_lookback_sec')}s "
        f"brief={debug.get('brief_lookback_sec')}s"
    )
    lines.append(
        f"since_utc: feed>={debug.get('feed_since')} "
        f"summary>={debug.get('summary_since')} "
        f"brief>={debug.get('brief_since')}"
    )
    lines.append(f"Loaded summaries: {debug.get('summaries_loaded', 0)}")
    for row in debug.get("summaries_detail") or []:
        lines.append(f"{row['symbol']}")
        hc = int(row.get("headline_count") or 0)
        lines.append(f"{hc} headline{'s' if hc != 1 else ''}")
        lines.append("")
    skipped = debug.get("skipped") or []
    lines.append("Skipped:")
    if not skipped:
        lines.append("(none)")
    else:
        for s in skipped:
            lines.append(s.get("symbol") or "?")
            lines.append("reason:")
            lines.append(str(s.get("reason") or ""))
            lines.append("")
    lines.append(f"Loaded briefs: {debug.get('briefs_loaded', 0)}")
    lines.append(f"Loaded feed items: {debug.get('feed_loaded', 0)}")
    lines.append(f"Generated assets: {debug.get('generated_assets', 0)}")
    lines.append(f"Active assets: {debug.get('active_assets', 0)}")
    lines.append(f"Summaries used: {debug.get('summaries_used', 0)}")
    return "\n".join(lines).rstrip() + "\n"


def run_narrative_engine_cycle_s42(
    *,
    window_sec: int = WINDOW_SEC,
    now: int | None = None,
    write_reports: bool = True,
    debug: bool = False,
    summary_lookback_sec: int | None = None,
    feed_lookback_sec: int | None = None,
    brief_lookback_sec: int | None = None,
) -> dict[str, Any]:
    """Hourly: per-asset intelligence from N11 + market_news_summary + briefs."""
    now_ts = int(now if now is not None else _utc_now())
    # window_sec kept for API compat; lookbacks default wider to match news-intel.
    sum_lb = int(summary_lookback_sec or max(int(window_sec), SUMMARY_LOOKBACK_SEC))
    feed_lb = int(feed_lookback_sec or max(int(window_sec), FEED_LOOKBACK_SEC))
    brief_lb = int(brief_lookback_sec or max(int(window_sec), BRIEF_LOOKBACK_SEC))
    feed_since = now_ts - feed_lb
    summary_since = now_ts - sum_lb
    brief_since = now_ts - brief_lb
    watch = set(watched_symbols())

    skipped: list[dict[str, str]] = []
    summaries_detail: list[dict[str, Any]] = []

    with market_events_connection() as conn:
        # Count totals for debug SQL evidence
        n11_total = 0
        sum_total = 0
        if _table_exists(conn, "market_news_feed_n11"):
            n11_total = int(
                conn.execute("SELECT COUNT(*) AS n FROM market_news_feed_n11")
                .fetchone()["n"]
            )
        if _table_exists(conn, "market_news_summary"):
            sum_total = int(
                conn.execute("SELECT COUNT(*) AS n FROM market_news_summary")
                .fetchone()["n"]
            )

        feed = _load_n11(conn, since_ts=feed_since)
        summaries_raw = _load_summaries_raw(conn, since_ts=summary_since)
        briefs = _load_briefs(conn, since_ts=brief_since)

        logger.info(
            "s42 load sql n11_total=%s selected_feed=%s since=%s | "
            "summary_total=%s selected_summaries=%s since=%s | "
            "selected_briefs=%s since=%s",
            n11_total,
            len(feed),
            feed_since,
            sum_total,
            len(summaries_raw),
            summary_since,
            len(briefs),
            brief_since,
        )

        # Classify summaries: watchlist match vs skip
        in_window_by_sym = _pick_latest_summary_per_symbol(summaries_raw)
        for sym, s in sorted(in_window_by_sym.items()):
            if sym not in watch:
                skipped.append({
                    "symbol": sym,
                    "reason": "not on watchlist",
                })
                continue
            summaries_detail.append({
                "symbol": sym,
                "headline_count": int(s.get("headline_count") or 0),
                "period_end": int(s.get("period_end") or 0),
                "importance": float(s.get("importance") or 0),
            })

        # Also note watchlist symbols with no summary
        for sym in sorted(watch):
            if sym not in in_window_by_sym:
                # Check if older summaries exist outside lookback
                older = None
                if _table_exists(conn, "market_news_summary"):
                    older = conn.execute(
                        """
                        SELECT period_end, created_at FROM market_news_summary
                        WHERE UPPER(symbol)=? ORDER BY period_end DESC LIMIT 1
                        """,
                        (sym,),
                    ).fetchone()
                if older:
                    pe = int(older["period_end"] or older["created_at"] or 0)
                    skipped.append({
                        "symbol": sym,
                        "reason": (
                            f"outside time window "
                            f"(latest period_end={pe}, need>={summary_since})"
                        ),
                    })

        summary_by_sym = {
            sym: row for sym, row in in_window_by_sym.items() if sym in watch
        }

        by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in feed:
            matched = False
            for sym in item.get("symbols") or []:
                sym_u = str(sym).upper()
                if sym_u in watch:
                    by_sym[sym_u].append(item)
                    matched = True
            if not matched and not (item.get("symbols") or []):
                # Untagged feed rows: try detect against watchlist
                detected = [
                    s for s in detect_symbols(
                        f"{item.get('title') or ''}\n{item.get('body') or ''}"
                    )
                    if s in watch
                ]
                for sym_u in detected:
                    by_sym[sym_u].append(item)

        assets: list[dict[str, Any]] = []
        for sym in watched_symbols():
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

    active = sum(1 for a in assets if int(a.get("news_count") or 0) > 0)
    summaries_used = len(summary_by_sym)

    debug_info = {
        "now_utc": now_ts,
        "feed_lookback_sec": feed_lb,
        "summary_lookback_sec": sum_lb,
        "brief_lookback_sec": brief_lb,
        "feed_since": feed_since,
        "summary_since": summary_since,
        "brief_since": brief_since,
        "n11_total": n11_total,
        "summary_total": sum_total,
        "feed_loaded": len(feed),
        "summaries_loaded": len(summaries_detail),
        "summaries_detail": summaries_detail,
        "briefs_loaded": len(briefs),
        "skipped": skipped,
        "generated_assets": written,
        "active_assets": active,
        "summaries_used": summaries_used,
    }

    logger.info(
        "s42 narrative cycle assets=%s active=%s feed=%s summaries=%s briefs=%s",
        written,
        active,
        len(feed),
        summaries_used,
        len(briefs),
    )
    if skipped:
        for s in skipped[:20]:
            logger.info(
                "s42 skipped summary symbol=%s reason=%s",
                s.get("symbol"),
                s.get("reason"),
            )

    result = {
        "created_at": now_ts,
        "window_sec": window_sec,
        "feed_lookback_sec": feed_lb,
        "summary_lookback_sec": sum_lb,
        "feed_items": len(feed),
        "summaries_used": summaries_used,
        "briefs_used": len(briefs),
        "assets_written": written,
        "active_assets": active,
        "reports": report_paths,
        "debug": debug_info,
    }
    if debug:
        print(format_narrative_debug_s42(debug_info), flush=True)
    return result
