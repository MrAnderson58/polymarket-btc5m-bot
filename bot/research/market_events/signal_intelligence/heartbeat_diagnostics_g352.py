"""Phase G.3.5.2 — heartbeat writer/reader diagnostics (fixes STALE while observe runs)."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state, set_g3_ops_state

_STALE_SEC = 180


def write_system_heartbeat(
    conn: Any,
    *,
    writer: str,
    status: str = "ok",
    latency_ms: int | None = None,
) -> None:
    """Record liveness from any running research loop (observe, g3-live, shock-paper)."""
    now = int(time.time())
    payload = {
        "writer": writer,
        "status": status,
        "latency_ms": latency_ms,
        "ts": now,
    }
    set_g3_ops_state(conn, "system_heartbeat", json.dumps(payload))
    set_g3_ops_state(conn, "system_heartbeat_writer", writer)
    set_g3_ops_state(conn, "system_heartbeat_ts", str(now))


def touch_heartbeat_reader(conn: Any) -> int:
    """Mark that status/dashboard/CLI read heartbeat state."""
    now = int(time.time())
    set_g3_ops_state(conn, "heartbeat_reader_ts", str(now))
    return now


def read_heartbeat_diagnostics(conn: Any, *, touch_reader: bool = True) -> dict[str, Any]:
    """Unified heartbeat view: telegram send + system writer + g3 cycle.

    touch_reader=False for pure RO telegram commands (/status,/health) — never write.
    """
    if touch_reader:
        touch_heartbeat_reader(conn)
    now = int(time.time())

    sched = conn.execute(
        "SELECT last_heartbeat_telegram_ts, updated_at FROM market_events_scheduler_state WHERE id = 1",
    ).fetchone()

    telegram_ts = int(sched["last_heartbeat_telegram_ts"] or 0) if sched else 0
    system_ts_raw = get_g3_ops_state(conn, "system_heartbeat_ts")
    system_ts = int(system_ts_raw) if system_ts_raw else 0
    g3_cycle_raw = get_g3_ops_state(conn, "last_cycle_ts")
    g3_cycle_ts = int(g3_cycle_raw) if g3_cycle_raw else 0
    reader_ts_raw = get_g3_ops_state(conn, "heartbeat_reader_ts")
    reader_ts = int(reader_ts_raw) if reader_ts_raw else 0

    system_json_raw = get_g3_ops_state(conn, "system_heartbeat")
    writer = get_g3_ops_state(conn, "system_heartbeat_writer")
    system_status = "unknown"
    writer_latency_ms = None
    if system_json_raw:
        try:
            blob = json.loads(system_json_raw)
            writer = blob.get("writer") or writer
            system_status = str(blob.get("status") or "ok")
            if blob.get("latency_ms") is not None:
                writer_latency_ms = int(blob["latency_ms"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    activity_ts = max(telegram_ts, system_ts, g3_cycle_ts)
    age_sec = (now - activity_ts) if activity_ts else None
    stale = age_sec is None or age_sec > _STALE_SEC

    if system_ts >= telegram_ts and system_ts >= g3_cycle_ts and system_ts:
        source = writer or "system"
    elif g3_cycle_ts >= telegram_ts and g3_cycle_ts:
        source = "g3-live"
    elif telegram_ts:
        source = "telegram"
    else:
        source = "none"

    return {
        "status": "STALE" if stale else "OK",
        "last_heartbeat_ts": activity_ts or None,
        "age_sec": age_sec,
        "writer": writer,
        "writer_source": source,
        "writer_status": system_status,
        "writer_latency_ms": writer_latency_ms,
        "telegram_heartbeat_ts": telegram_ts or None,
        "system_heartbeat_ts": system_ts or None,
        "g3_cycle_ts": g3_cycle_ts or None,
        "reader_ts": reader_ts or None,
        "stale_threshold_sec": _STALE_SEC,
    }


def format_heartbeat_trace(conn: Any) -> str:
    d = read_heartbeat_diagnostics(conn)
    lines = [
        "Heartbeat Trace",
        "",
        "Status",
        str(d["status"]),
        "",
        "Last heartbeat",
        str(d["last_heartbeat_ts"] or "—"),
        "",
        "Writer",
        str(d["writer"] or "—"),
        "",
        "Reader",
        str(d["reader_ts"] or "—"),
        "",
        "Latency",
        f"{d['writer_latency_ms']} ms" if d["writer_latency_ms"] is not None else "—",
        "",
        "Age",
        f"{d['age_sec']}s" if d["age_sec"] is not None else "—",
        "",
        "Sources",
        f"telegram={d['telegram_heartbeat_ts']}",
        f"system={d['system_heartbeat_ts']}",
        f"g3_cycle={d['g3_cycle_ts']}",
    ]
    return "\n".join(lines)


def append_heartbeat_to_status(
    base_report: str,
    conn: Any | None = None,
    *,
    touch_reader: bool = False,
) -> str:
    """Append heartbeat block. Default: no writes (G0.5 pure RO /status,/health)."""
    if conn is not None:
        d = read_heartbeat_diagnostics(conn, touch_reader=touch_reader)
    else:
        from bot.research.market_events.db import market_events_readonly_connection

        with market_events_readonly_connection() as ro:
            d = read_heartbeat_diagnostics(ro, touch_reader=False)
    extra = "\n".join([
        "",
        "Heartbeat",
        str(d["status"]),
        "",
        "Writer",
        str(d["writer"] or "—"),
        "",
        "Age",
        f"{d['age_sec']}s" if d["age_sec"] is not None else "—",
    ])
    return base_report + extra


def heartbeat_status_line(conn: Any) -> str:
    d = read_heartbeat_diagnostics(conn)
    age = d["age_sec"]
    age_s = f"{age}s ago" if age is not None else "never"
    return f"Heartbeat: {d['status']} ({age_s}, writer={d['writer'] or '—'})"
