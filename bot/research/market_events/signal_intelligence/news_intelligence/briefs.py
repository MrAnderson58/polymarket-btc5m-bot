"""S41 — global market brief every 2 hours → market_daily_briefs."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from bot.research.market_events.db import execute_with_retry, market_events_connection

logger = logging.getLogger(__name__)

BRIEF_WINDOW_SEC = 2 * 3600

_MACRO_KW = ("fed", "fomc", "cpi", "inflation", "interest rate", "treasury")
_ETF_KW = ("etf", "blackrock", "grayscale", "ishares")
_WHALE_KW = ("whale", "large transfer", "outflow", "inflow", "exchange deposit")
_POLY_KW = ("polymarket", "prediction market", "odds")


def _load_summaries(conn: Any, *, since_ts: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, period_start, period_end, symbol, summary,
               bullish_score, bearish_score, neutral_score,
               importance, sources, headline_count, created_at
        FROM market_news_summary
        WHERE period_end >= ?
        ORDER BY importance DESC, headline_count DESC
        LIMIT 200
        """,
        (since_ts,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except Exception:
            d["sources"] = []
        out.append(d)
    return out


def _match_any(text: str, kws: tuple[str, ...]) -> bool:
    blob = text.lower()
    return any(k in blob for k in kws)


def build_global_brief(summaries: list[dict[str, Any]], *, now: int) -> dict[str, Any]:
    bullish = sorted(
        [s for s in summaries if s.get("symbol") not in ("MACRO",)],
        key=lambda s: float(s.get("bullish_score") or 0) * float(s.get("importance") or 0),
        reverse=True,
    )[:5]
    bearish = sorted(
        [s for s in summaries if s.get("symbol") not in ("MACRO",)],
        key=lambda s: float(s.get("bearish_score") or 0) * float(s.get("importance") or 0),
        reverse=True,
    )[:5]

    narratives = [str(s.get("summary") or "").split("\n")[0] for s in summaries[:5]]
    macro_events = [
        str(s.get("summary") or "").split("\n")[0]
        for s in summaries
        if _match_any(str(s.get("summary") or ""), _MACRO_KW)
    ][:5]
    etf = [
        str(s.get("summary") or "").split("\n")[0]
        for s in summaries
        if _match_any(str(s.get("summary") or ""), _ETF_KW)
    ][:5]
    whales = [
        str(s.get("summary") or "").split("\n")[0]
        for s in summaries
        if _match_any(str(s.get("summary") or ""), _WHALE_KW)
    ][:5]
    polymarket = [
        str(s.get("summary") or "").split("\n")[0]
        for s in summaries
        if _match_any(str(s.get("summary") or ""), _POLY_KW)
    ][:5]

    avg_bear = (
        sum(float(s.get("bearish_score") or 0) for s in summaries) / len(summaries)
        if summaries
        else 0.2
    )
    avg_imp = (
        sum(float(s.get("importance") or 0) for s in summaries) / len(summaries)
        if summaries
        else 0.3
    )
    risk_score = round(min(1.0, 0.4 * avg_bear + 0.6 * avg_imp), 3)
    if risk_score >= 0.7:
        risk_level = "HIGH"
    elif risk_score >= 0.45:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    global_narrative = (
        narratives[0]
        if narratives
        else "Limited crypto news flow in this window — monitoring watchlist."
    )
    if len(narratives) > 1:
        global_narrative += " Secondary themes: " + "; ".join(narratives[1:3])

    return {
        "created_at": now,
        "global_narrative": global_narrative[:2000],
        "top_bullish": [
            {
                "symbol": s["symbol"],
                "score": s["bullish_score"],
                "summary": s["summary"][:240],
            }
            for s in bullish
        ],
        "top_bearish": [
            {
                "symbol": s["symbol"],
                "score": s["bearish_score"],
                "summary": s["summary"][:240],
            }
            for s in bearish
        ],
        "macro_events": macro_events,
        "fed": [e for e in macro_events if "fed" in e.lower() or "fomc" in e.lower()],
        "etf": etf,
        "whales": whales,
        "polymarket": polymarket,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "headline_count": sum(int(s.get("headline_count") or 0) for s in summaries),
    }


def save_daily_brief(
    conn: Any,
    brief: dict[str, Any],
    *,
    period_start: int,
    period_end: int,
) -> int:
    cur = execute_with_retry(
        conn,
        """
        INSERT INTO market_daily_briefs (
          period_start, period_end, global_narrative,
          top_bullish_json, top_bearish_json,
          macro_events_json, fed_json, etf_json, whales_json, polymarket_json,
          risk_level, risk_score, headline_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            period_start,
            period_end,
            brief["global_narrative"],
            json.dumps(brief.get("top_bullish") or [], ensure_ascii=False),
            json.dumps(brief.get("top_bearish") or [], ensure_ascii=False),
            json.dumps(brief.get("macro_events") or [], ensure_ascii=False),
            json.dumps(brief.get("fed") or [], ensure_ascii=False),
            json.dumps(brief.get("etf") or [], ensure_ascii=False),
            json.dumps(brief.get("whales") or [], ensure_ascii=False),
            json.dumps(brief.get("polymarket") or [], ensure_ascii=False),
            brief["risk_level"],
            float(brief["risk_score"]),
            int(brief.get("headline_count") or 0),
            int(brief["created_at"]),
        ),
    )
    return int(getattr(cur, "lastrowid", 0) or 0)


def run_global_brief_cycle_s41(
    *,
    window_sec: int = BRIEF_WINDOW_SEC,
    now: int | None = None,
) -> dict[str, Any]:
    now_ts = int(now if now is not None else time.time())
    period_end = now_ts
    period_start = now_ts - int(window_sec)

    with market_events_connection() as conn:
        summaries = _load_summaries(conn, since_ts=period_start)
        brief = build_global_brief(summaries, now=now_ts)
        brief_id = save_daily_brief(
            conn, brief, period_start=period_start, period_end=period_end,
        )
        conn.commit()

    logger.info(
        "s41 brief id=%s risk=%s summaries=%s",
        brief_id,
        brief["risk_level"],
        len(summaries),
    )
    return {
        "brief_id": brief_id,
        "period_start": period_start,
        "period_end": period_end,
        "risk_level": brief["risk_level"],
        "summaries_used": len(summaries),
        "brief": brief,
    }
