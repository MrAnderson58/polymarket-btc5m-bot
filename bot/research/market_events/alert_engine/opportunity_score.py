"""Phase E.5 — research-only opportunity score (does not affect paper trading)."""

from __future__ import annotations

import json
import time
from typing import Any


def compute_opportunity_score(conn: Any, *, event_id: int) -> dict[str, Any]:
    """Fixed-weight score 0–100 from history, vol, liquidity, context, AI."""
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return {"event_id": event_id, "score": 0.0, "components": {}}

    ret = abs(float(row["return_pct"] or 0))
    vol_z = abs(float(row["volume_zscore"] or 0))
    spread_proxy = abs(float(row["relative_return_pct"] or 0))

    hist = conn.execute(
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN p.confirmed_reversal IS NOT NULL AND p.confirmed_reversal != '' THEN 1 ELSE 0 END) AS rev
        FROM market_events e
        LEFT JOIN market_events_pending_shocks p ON p.event_id = e.id
        WHERE e.symbol = ? AND e.id != ? AND ABS(e.return_pct) >= ?
        """,
        (row["symbol"], event_id, max(1.0, ret * 0.65)),
    ).fetchone()
    hist_n = int(hist["n"] if hist else 0)
    hist_rev_rate = (int(hist["rev"]) / hist_n) if hist_n else 0.5

    ctx_n = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_context WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    ctx_count = int(ctx_n["n"] if ctx_n else 0)

    ai = conn.execute(
        "SELECT reversal_bias, confidence FROM market_event_ai_analyses WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    ai_fade = 0.5
    if ai:
        if ai["reversal_bias"] == "FADE_FAVORED":
            ai_fade = 0.8
        elif ai["reversal_bias"] == "CONTINUATION_FAVORED":
            ai_fade = 0.2
        ai_fade *= float(ai["confidence"] or 0.5)

    news_n = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_context WHERE event_id = ? AND context_type = 'NEWS'",
        (event_id,),
    ).fetchone()
    has_news = int(news_n["n"] if news_n else 0) > 0

    components = {
        "history_reversal_rate": round(hist_rev_rate, 3),
        "impulse_magnitude": round(min(ret / 5.0, 1.0), 3),
        "volume_anomaly": round(min(vol_z / 3.0, 1.0), 3),
        "liquidity_spread_proxy": round(max(0, 1.0 - spread_proxy / 3.0), 3),
        "telegram_context": round(min(ctx_count / 3.0, 1.0), 3),
        "news_catalyst": 0.7 if has_news else 0.3,
        "ai_fade_signal": round(ai_fade, 3),
    }
    weights = {
        "history_reversal_rate": 25,
        "impulse_magnitude": 15,
        "volume_anomaly": 10,
        "liquidity_spread_proxy": 10,
        "telegram_context": 15,
        "news_catalyst": 10,
        "ai_fade_signal": 15,
    }
    score = sum(components[k] * weights[k] for k in weights)
    score = round(min(100.0, max(0.0, score)), 1)

    return {"event_id": event_id, "score": score, "components": components}


def persist_opportunity_score(conn: Any, *, event_id: int) -> float:
    result = compute_opportunity_score(conn, event_id=event_id)
    now = int(time.time())
    conn.execute(
        """
        INSERT OR REPLACE INTO market_events_opportunity_scores (
          event_id, score, components_json, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (event_id, result["score"], json.dumps(result["components"]), now),
    )
    return result["score"]
