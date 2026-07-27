"""Phase G.3.5.2 — heartbeat writer/reader diagnostics (fixes STALE while observe runs)."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state, set_g3_ops_state

_STALE_SEC = 180
# Ops heartbeat does not need 1Hz dual-writer upserts; stale threshold is 180s.
_HEARTBEAT_MIN_INTERVAL_SEC = float(os.getenv("ME_SYSTEM_HEARTBEAT_MIN_INTERVAL_SEC", "20"))
_last_heartbeat_write_mono: float = 0.0


def write_system_heartbeat(
    conn: Any,
    *,
    writer: str,
    status: str = "ok",
    latency_ms: int | None = None,
    force: bool = False,
) -> bool:
    """Record liveness from any running research loop (observe, g3-live, shock-paper).

    Debounced to cut SQLite contention: at most one batched write per process per
    ``ME_SYSTEM_HEARTBEAT_MIN_INTERVAL_SEC`` (default 20s). Returns True if written.
    """
    global _last_heartbeat_write_mono
    now_mono = time.monotonic()
    if not force and (now_mono - _last_heartbeat_write_mono) < _HEARTBEAT_MIN_INTERVAL_SEC:
        return False

    from bot.research.market_events.db import retry_on_db_locked

    now = int(time.time())
    payload = {
        "writer": writer,
        "status": status,
        "latency_ms": latency_ms,
        "ts": now,
    }
    rows = (
        ("system_heartbeat", json.dumps(payload), now),
        ("system_heartbeat_writer", writer, now),
        ("system_heartbeat_ts", str(now), now),
    )
    sql = """
        INSERT INTO market_events_g3_ops_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """

    def _batch() -> None:
        if hasattr(conn, "executemany"):
            conn.executemany(sql, rows)
        else:
            for key, value, _ts in rows:
                set_g3_ops_state(conn, key, value)

    retry_on_db_locked(_batch)
    _last_heartbeat_write_mono = now_mono
    return True


def touch_heartbeat_reader(conn: Any) -> int:
    """Mark that status/dashboard/CLI read heartbeat state."""
    now = int(time.time())
    set_g3_ops_state(conn, "heartbeat_reader_ts", str(now))
    return now


def read_heartbeat_diagnostics(conn: Any, *, touch_reader: bool = False) -> dict[str, Any]:
    """Unified heartbeat view: telegram send + system writer + g3 cycle.

    Default touch_reader=False — /status must never write heartbeat (S2.2).
    Opt-in writes only when an explicit writer path needs a reader mark.
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
