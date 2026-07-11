"""Opportunity Score v2 — extended research-only scoring."""

from __future__ import annotations

import json
import time
from typing import Any


def compute_opportunity_score_v2(conn: Any, *, event_id: int) -> dict[str, Any]:
    from bot.research.market_events.alert_engine.opportunity_score import compute_opportunity_score

    base = compute_opportunity_score(conn, event_id=event_id)
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return {"event_id": event_id, "score": 0.0, "breakdown": {}}

    breakdown: dict[str, float] = {}

    hist_rate = float(base.get("components", {}).get("history_reversal_rate", 0.5))
    breakdown["History"] = round(hist_rate * 25, 1)

    liq = float(base.get("components", {}).get("liquidity_spread_proxy", 0.5))
    breakdown["Liquidity"] = round(liq * 20, 1)

    vol = float(base.get("components", {}).get("volume_anomaly", 0.5))
    breakdown["Volatility"] = round(vol * 12, 1)

    tg = float(base.get("components", {}).get("telegram_context", 0))
    breakdown["Telegram"] = round(tg * 18, 1)

    news = float(base.get("components", {}).get("news_catalyst", 0.3))
    breakdown["News"] = round(news * 10, 1)

    ctx = conn.execute(
        "SELECT funding, open_interest FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    funding_score = 0.0
    oi_score = 0.0
    if ctx and ctx["funding"] is not None:
        funding_score = min(abs(float(ctx["funding"])) * 40, 10)
    if ctx and ctx["open_interest"] is not None:
        oi_score = 5.0
    breakdown["Funding"] = round(funding_score, 1)
    breakdown["OI"] = round(oi_score, 1)

    exh = conn.execute(
        "SELECT exhaustion_score FROM market_events_exhaustion WHERE source_event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    breakdown["Trend exhaustion"] = round(float(exh["exhaustion_score"]) * 0.2, 1) if exh else 0.0

    mtf = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_multitimeframe WHERE source_event_id = ?",
        (event_id,),
    ).fetchone()
    mtf_n = int(mtf["n"] if mtf else 0)
    breakdown["Cross exchange confirmation"] = round(min(mtf_n * 3, 12), 1)

    score = round(min(100.0, sum(breakdown.values())), 1)
    return {"event_id": event_id, "score": score, "breakdown": breakdown}


def persist_opportunity_score_v2(conn: Any, *, event_id: int) -> float:
    result = compute_opportunity_score_v2(conn, event_id=event_id)
    now = int(time.time())
    conn.execute(
        """
        INSERT OR REPLACE INTO market_events_opportunity_scores_v2 (
          event_id, score, score_breakdown_json, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (event_id, result["score"], json.dumps(result["breakdown"]), now),
    )
    return result["score"]


def format_score_breakdown(breakdown: dict[str, float]) -> str:
    lines = []
    for key, val in breakdown.items():
        sign = "+" if val >= 0 else ""
        lines.append(f"{key}\n{sign}{val:.0f}")
    return "\n".join(lines)
