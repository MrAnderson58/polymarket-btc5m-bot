"""Phase E.5 — event timeline builder."""

from __future__ import annotations

import json
import time
from typing import Any


def build_event_timeline(conn: Any, *, event_id: int) -> list[dict[str, Any]]:
    """Shock → Telegram → News → AI → Reversal → Paper → Exit."""
    steps: list[dict[str, Any]] = []
    ev = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not ev:
        return steps

    steps.append({
        "stage": "Shock",
        "ts": int(ev["event_ts"]),
        "detail": f"{ev['symbol']} {ev['return_pct']:+.2f}% {ev['direction']}",
    })

    for ctx in conn.execute(
        """
        SELECT context_type, source, context_ts FROM market_event_context
        WHERE event_id = ? AND context_type IN (
          'TELEGRAM_SIGNAL', 'TRADER_THESIS', 'MARKET_COMMENTARY'
        )
        ORDER BY context_ts ASC LIMIT 5
        """,
        (event_id,),
    ).fetchall():
        steps.append({
            "stage": "Telegram",
            "ts": int(ctx["context_ts"]),
            "detail": f"{ctx['context_type']} ({ctx['source']})",
        })

    for ctx in conn.execute(
        """
        SELECT source, context_ts FROM market_event_context
        WHERE event_id = ? AND context_type = 'NEWS'
        ORDER BY context_ts ASC LIMIT 3
        """,
        (event_id,),
    ).fetchall():
        steps.append({
            "stage": "News",
            "ts": int(ctx["context_ts"]),
            "detail": ctx["source"],
        })

    ai = conn.execute(
        "SELECT created_at, reversal_bias FROM market_event_ai_analyses WHERE event_id = ? ORDER BY created_at LIMIT 1",
        (event_id,),
    ).fetchone()
    if ai:
        steps.append({
            "stage": "AI",
            "ts": int(ai["created_at"]),
            "detail": ai["reversal_bias"],
        })

    pending = conn.execute(
        "SELECT * FROM market_events_pending_shocks WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if pending and pending["confirmed_reversal"]:
        steps.append({
            "stage": "Reversal",
            "ts": int(pending["updated_at"] or ev["event_ts"]),
            "detail": f"confirmed latency={pending['confirm_latency_sec']}s",
        })

    for run in conn.execute(
        """
        SELECT entry_ts, reversal_variant, exit_variant, entry_price
        FROM paper_strategy_runs WHERE event_id = ? AND entry_ts IS NOT NULL
        ORDER BY entry_ts LIMIT 3
        """,
        (event_id,),
    ).fetchall():
        steps.append({
            "stage": "Paper",
            "ts": int(run["entry_ts"]),
            "detail": f"{run['reversal_variant']}/{run['exit_variant']} @ {run['entry_price']}",
        })

    for run in conn.execute(
        """
        SELECT exit_ts, exit_reason, net_return, reversal_variant, exit_variant
        FROM paper_strategy_runs WHERE event_id = ? AND exit_ts IS NOT NULL
        ORDER BY exit_ts LIMIT 3
        """,
        (event_id,),
    ).fetchall():
        steps.append({
            "stage": "Exit",
            "ts": int(run["exit_ts"]),
            "detail": f"{run['exit_reason']} {run['net_return']:+.2f}% ({run['reversal_variant']}/{run['exit_variant']})",
        })

    steps.sort(key=lambda s: s["ts"])
    return steps


def persist_timeline_cache(conn: Any, *, event_id: int) -> list[dict[str, Any]]:
    timeline = build_event_timeline(conn, event_id=event_id)
    now = int(time.time())
    conn.execute(
        """
        INSERT OR REPLACE INTO market_events_timeline_cache (
          event_id, timeline_json, updated_at
        ) VALUES (?, ?, ?)
        """,
        (event_id, json.dumps(timeline), now),
    )
    return timeline


def format_timeline_text(timeline: list[dict[str, Any]]) -> str:
    lines = ["EVENT TIMELINE", ""]
    for step in timeline:
        lines.append(f"{step['stage']}")
        lines.append(f"  ↓ {step['detail']}")
        lines.append("")
    return "\n".join(lines).strip()
