"""Phase E.5 — weekly detailed research report."""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from typing import Any

WEEKLY_ASSETS = ("BTC", "ETH", "SOL", "GOLD", "OIL", "NASDAQ", "TESLA")


def _week_bounds(now: int | None = None) -> tuple[int, int, str]:
    now = now or int(time.time())
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    week_key = f"{dt.year}-W{dt.isocalendar()[1]:02d}"
    start = now - 7 * 86400
    return start, now, week_key


def _asset_stats(conn: Any, symbol: str, start: int, end: int) -> dict[str, Any]:
    shocks = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE symbol = ? AND event_ts >= ? AND event_ts < ?",
        (symbol, start, end),
    ).fetchone()
    revs = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_pending_shocks p
        JOIN market_events e ON e.id = p.event_id
        WHERE e.symbol = ? AND p.confirmed_reversal IS NOT NULL AND p.confirmed_reversal != '' AND e.event_ts >= ?
        """,
        (symbol, start),
    ).fetchone()
    paper = conn.execute(
        """
        SELECT r.net_return FROM paper_strategy_runs r
        JOIN market_events e ON e.id = r.event_id
        WHERE e.symbol = ? AND r.entry_ts >= ?
        """,
        (symbol, start),
    ).fetchall()
    pnl_vals = [float(r["net_return"]) for r in paper if r["net_return"] is not None]
    latencies = conn.execute(
        """
        SELECT p.confirm_latency_sec FROM market_events_pending_shocks p
        JOIN market_events e ON e.id = p.event_id
        WHERE e.symbol = ? AND p.confirm_latency_sec IS NOT NULL AND e.event_ts >= ?
        """,
        (symbol, start),
    ).fetchall()
    lat_list = [int(r["confirm_latency_sec"]) for r in latencies if r["confirm_latency_sec"]]

    false_signals = int(shocks["n"] or 0) - int(revs["n"] or 0) if shocks else 0
    return {
        "symbol": symbol,
        "shocks": int(shocks["n"] if shocks else 0),
        "reversals": int(revs["n"] if revs else 0),
        "paper_pnl_avg": statistics.mean(pnl_vals) if pnl_vals else None,
        "false_signals": max(0, false_signals),
        "avg_latency_sec": int(statistics.mean(lat_list)) if lat_list else None,
    }


def build_weekly_report(conn: Any, *, now: int | None = None) -> tuple[str, str]:
    start, end, week_key = _week_bounds(now)
    lines = ["📈 WEEKLY REPORT", ""]

    syms_in_db = {
        r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM market_events WHERE event_ts >= ?",
            (start,),
        ).fetchall()
    }
    assets = list(WEEKLY_ASSETS) + [s for s in sorted(syms_in_db) if s not in WEEKLY_ASSETS][:5]

    for sym in assets:
        st = _asset_stats(conn, sym, start, end)
        if st["shocks"] == 0 and st["reversals"] == 0:
            continue
        lines.append(sym)
        lines.append(f"  shocks: {st['shocks']}")
        lines.append(f"  reversals: {st['reversals']}")
        pnl = st["paper_pnl_avg"]
        lines.append(f"  paper pnl avg: {pnl:+.2f}%" if pnl is not None else "  paper pnl avg: —")
        lines.append(f"  false signals: {st['false_signals']}")
        lat = st["avg_latency_sec"]
        lines.append(f"  average latency: {lat}s" if lat else "  average latency: —")
        lines.append("")

    if len(lines) <= 2:
        lines.append("No events in the last 7 days.")
    return "\n".join(lines), week_key


def should_send_weekly(conn: Any, *, now: int | None = None) -> bool:
    from bot.research.market_events.alert_engine.config import WEEKLY_DIGEST_WEEKDAY, weekly_digest_enabled
    if not weekly_digest_enabled():
        return False
    now = now or int(time.time())
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    if dt.weekday() != WEEKLY_DIGEST_WEEKDAY or dt.hour < 20:
        return False
    _, _, week_key = _week_bounds(now)
    row = conn.execute(
        "SELECT id FROM market_events_digest_log WHERE digest_type = 'weekly' AND period_key = ?",
        (week_key,),
    ).fetchone()
    return row is None


def send_weekly_report(conn: Any, *, now: int | None = None) -> bool:
    msg, week_key = build_weekly_report(conn, now=now)
    from bot.research.market_events.market_event_alerts import _safe_alert
    sent = _safe_alert(
        conn, event_id=0, alert_type="WEEKLY_REPORT", detail=week_key,
        message=msg, enabled=True,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO market_events_digest_log (
          digest_type, period_key, message_text, sent, created_at
        ) VALUES ('weekly', ?, ?, ?, ?)
        """,
        (week_key, msg, 1 if sent else 0, int(time.time())),
    )
    conn.execute(
        "UPDATE market_events_scheduler_state SET last_weekly_digest_ts = ?, updated_at = ? WHERE id = 1",
        (int(time.time()), int(time.time())),
    )
    return sent
