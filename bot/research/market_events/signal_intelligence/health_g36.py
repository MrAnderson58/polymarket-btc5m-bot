"""Phase G.3.6 — extended health report."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.health_g3 import format_g3_health_report, get_g3_ops_state


def format_g36_health_report(conn: Any) -> str:
    base = format_g3_health_report(conn)

    vision_ts = get_g3_ops_state(conn, "g36_last_vision_ts")
    memory_ts = get_g3_ops_state(conn, "last_memory_ts")
    memory_rows = get_g3_ops_state(conn, "memory_rows_last")

    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import is_claude_configured
        claude_ok = is_claude_configured()
    except Exception:
        claude_ok = False

    memory_count = 0
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM market_market_memory").fetchone()
        memory_count = int(row["n"] or 0) if row else 0
    except Exception:
        pass

    vision_count = 0
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM market_telegram_vision_g36").fetchone()
        vision_count = int(row["n"] or 0) if row else 0
    except Exception:
        pass

    recorder_raw = get_g3_ops_state(conn, "recorder_health")
    recorder_ok = "ok"
    if recorder_raw:
        import json
        try:
            recorder_ok = json.loads(recorder_raw).get("status", "unknown")
        except Exception:
            pass

    lines = [
        base,
        "",
        "— G3.6 Subsystems —",
        "",
        "Telegram",
        "OK",
        "",
        "Vision",
        f"OK ({vision_count} analyses)" if vision_ts else "Ready",
        "",
        "Claude",
        "OK" if claude_ok else "Not configured",
        "",
        "Recorder",
        str(recorder_ok),
        "",
        "G3",
        "OK" if get_g3_ops_state(conn, "last_cycle_ts") else "No cycle yet",
        "",
        "Memory",
        f"OK rows={memory_count} last={memory_rows or '—'} ts={memory_ts or '—'}",
        "",
        "Dashboard",
        "OK",
    ]
    return "\n".join(lines)
