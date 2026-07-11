"""Phase E.5 — AI prediction vs reality comparison."""

from __future__ import annotations

import json
import time
from typing import Any


def _verdict(prediction: dict, reality: dict) -> str:
    bias = prediction.get("reversal_bias", "NO_VIEW")
    confirmed = reality.get("confirmed_reversal", False)
    fade_won = reality.get("fade_profitable", False)

    if bias == "FADE_FAVORED":
        if confirmed and fade_won:
            return "correct"
        if confirmed and not fade_won:
            return "incorrect"
        if not confirmed:
            return "partial"
    if bias == "CONTINUATION_FAVORED":
        if not confirmed:
            return "correct"
        return "incorrect"
    if bias == "WAIT_FOR_CONFIRMATION":
        return "partial" if confirmed else "partial"
    return "partial"


def build_reality(conn: Any, *, event_id: int) -> dict[str, Any]:
    pending = conn.execute(
        "SELECT * FROM market_events_pending_shocks WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    runs = conn.execute(
        """
        SELECT net_return FROM paper_strategy_runs
        WHERE event_id = ? AND exit_ts IS NOT NULL
        """,
        (event_id,),
    ).fetchall()
    returns = [float(r["net_return"]) for r in runs if r["net_return"] is not None]
    fade_profitable = any(r > 0 for r in returns) if returns else False

    return {
        "confirmed_reversal": bool(pending and pending["confirmed_reversal"]),
        "confirm_latency_sec": int(pending["confirm_latency_sec"]) if pending and pending["confirm_latency_sec"] else None,
        "fade_profitable": fade_profitable,
        "paper_runs_closed": len(returns),
        "avg_paper_return": sum(returns) / len(returns) if returns else None,
    }


def run_ai_comparison(conn: Any, *, event_id: int) -> dict[str, Any] | None:
    ai = conn.execute(
        """
        SELECT structured_output_json, reversal_bias, movement_interpretation, confidence
        FROM market_event_ai_analyses WHERE event_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not ai:
        return None

    prediction = json.loads(ai["structured_output_json"] or "{}")
    prediction["reversal_bias"] = ai["reversal_bias"]
    reality = build_reality(conn, event_id=event_id)
    verdict = _verdict(prediction, reality)
    now = int(time.time())

    conn.execute(
        """
        INSERT OR REPLACE INTO market_events_ai_comparisons (
          event_id, prediction_json, reality_json, verdict, compared_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, json.dumps(prediction), json.dumps(reality), verdict, now),
    )
    return {"event_id": event_id, "verdict": verdict, "prediction": prediction, "reality": reality}


def comparison_report(conn: Any, *, days: int = 7) -> str:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT verdict, COUNT(*) AS n FROM market_events_ai_comparisons
        WHERE compared_at >= ? GROUP BY verdict
        """,
        (since,),
    ).fetchall()
    lines = ["AI COMPARISON REPORT", f"period_days: {days}", ""]
    if not rows:
        lines.append("No comparisons yet.")
        return "\n".join(lines)
    for r in rows:
        lines.append(f"  {r['verdict']}: {r['n']}")
    return "\n".join(lines)
