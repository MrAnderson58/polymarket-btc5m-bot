"""Phase G.3 — ops health and recorder status."""

from __future__ import annotations

import json
import time
from typing import Any


def set_g3_ops_state(conn: Any, key: str, value: str) -> None:
    from bot.research.market_events.db import execute_with_retry

    now = int(time.time())
    execute_with_retry(
        conn,
        """
        INSERT INTO market_events_g3_ops_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now),
    )


def get_g3_ops_state(conn: Any, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM market_events_g3_ops_state WHERE key = ?",
        (key,),
    ).fetchone()
    return str(row["value"]) if row else None


def update_recorder_health_g3(
    conn: Any,
    *,
    snapshot_id: int | None,
    latency_ms: float | None,
    status: str = "ok",
    error: str | None = None,
) -> None:
    payload = {
        "snapshot_id": snapshot_id,
        "latency_ms": latency_ms,
        "status": status,
        "error": error,
        "updated_at": int(time.time()),
    }
    set_g3_ops_state(conn, "recorder_health", json.dumps(payload))


def format_g3_health_report(conn: Any) -> str:
    health_raw = get_g3_ops_state(conn, "recorder_health")
    last_cycle = get_g3_ops_state(conn, "last_cycle_ts")
    try:
        health = json.loads(health_raw) if health_raw else {}
    except (json.JSONDecodeError, TypeError):
        health = {}

    snap_count = conn.execute("SELECT COUNT(*) AS n FROM market_snapshots_g3").fetchone()
    signal_count = conn.execute(
        "SELECT COUNT(*) AS n FROM market_live_signals_g3 WHERE created_at > ?",
        (int(time.time()) - 86400,),
    ).fetchone()
    active = conn.execute(
        "SELECT COUNT(*) AS n FROM market_live_signals_g3 WHERE status IN ('ACTIVE', 'TP1_HIT')",
    ).fetchone()

    lines = [
        "G3 Live Signal Engine — Health",
        "",
        f"Recorder status: {health.get('status', 'unknown')}",
        f"Last snapshot id: {health.get('snapshot_id', '—')}",
        f"Collector latency: {health.get('latency_ms', '—')} ms",
        f"Last cycle: {last_cycle or '—'}",
        f"Total snapshots: {int(snap_count['n'] or 0)}",
        f"Signals (24h): {int(signal_count['n'] or 0)}",
        f"Active signals: {int(active['n'] or 0)}",
    ]
    if health.get("error"):
        lines.append(f"Last error: {health['error']}")
    return "\n".join(lines)
