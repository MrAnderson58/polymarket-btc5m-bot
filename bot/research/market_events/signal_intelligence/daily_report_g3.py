"""Phase G.3 — daily Telegram digest at 09:00 local."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import G3_DAILY_REPORT_HOUR_LOCAL

logger = logging.getLogger(__name__)

_DEFAULT_TZ = "Europe/Moscow"


def _local_tz() -> ZoneInfo:
    import os
    name = os.getenv("ME_G3_REPORT_TZ", _DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _yesterday_bounds() -> tuple[int, int, str]:
    tz = _local_tz()
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_local.hour < G3_DAILY_REPORT_HOUR_LOCAL:
        start_local = start_local.fromtimestamp(start_local.timestamp() - 86400, tz=tz)
    end_local = start_local.replace(hour=23, minute=59, second=59)
    day_start = int(start_local.astimezone(timezone.utc).timestamp()) - 86400
    day_end = day_start + 86400 - 1
    report_date = datetime.fromtimestamp(day_start, tz=tz).strftime("%Y-%m-%d")
    return day_start, day_end, report_date


def build_daily_report_g3(conn: Any) -> tuple[str, dict[str, Any]]:
    day_start, day_end, report_date = _yesterday_bounds()

    generated = conn.execute(
        "SELECT COUNT(*) AS n FROM market_live_signals_g3 WHERE created_at BETWEEN ? AND ?",
        (day_start, day_end),
    ).fetchone()
    sent = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_live_signals_g3
        WHERE created_at BETWEEN ? AND ? AND telegram_sent = 1 AND dashboard_only = 0
        """,
        (day_start, day_end),
    ).fetchone()
    filtered = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_live_signals_g3
        WHERE created_at BETWEEN ? AND ? AND dashboard_only = 1
        """,
        (day_start, day_end),
    ).fetchone()
    closed = conn.execute(
        """
        SELECT pnl_pct, risk_reward, liquidity_state, symbol
        FROM market_live_signals_g3
        WHERE closed_at BETWEEN ? AND ? AND status = 'CLOSED'
        """,
        (day_start, day_end),
    ).fetchall()

    wins = [r for r in closed if r["pnl_pct"] and float(r["pnl_pct"]) > 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    avg_rr = sum(float(r["risk_reward"] or 0) for r in closed) / len(closed) if closed else 0.0

    best = worst = None
    if closed:
        best = max(closed, key=lambda r: float(r["pnl_pct"] or -999))
        worst = min(closed, key=lambda r: float(r["pnl_pct"] or 999))

    claude_row = conn.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost
        FROM market_events_ai_research_g2
        WHERE created_at BETWEEN ? AND ?
        """,
        (day_start, day_end),
    ).fetchone()

    pattern_row = conn.execute(
        """
        SELECT pattern_type, COUNT(*) AS n FROM market_trend_windows_g3
        WHERE created_at BETWEEN ? AND ?
        GROUP BY pattern_type ORDER BY n DESC LIMIT 1
        """,
        (day_start, day_end),
    ).fetchone()

    report = {
        "report_date": report_date,
        "signals_generated": int(generated["n"] or 0),
        "signals_sent": int(sent["n"] or 0),
        "signals_filtered": int(filtered["n"] or 0),
        "win_rate": round(win_rate, 3),
        "avg_rr": round(avg_rr, 2),
        "best_setup": f"{best['symbol']} {best['liquidity_state']}" if best else "—",
        "worst_setup": f"{worst['symbol']} {worst['liquidity_state']}" if worst else "—",
        "claude_requests": int(claude_row["n"] or 0),
        "claude_cost_usd": round(float(claude_row["cost"] or 0), 4),
        "top_pattern": pattern_row["pattern_type"] if pattern_row else "—",
    }

    msg = "\n".join([
        f"📅 G3 Daily Report — {report_date}",
        "",
        "Yesterday",
        "",
        "Signals generated",
        str(report["signals_generated"]),
        "",
        "Signals filtered",
        str(report["signals_filtered"]),
        "",
        "Win Rate",
        f"{report['win_rate'] * 100:.0f}%",
        "",
        "Average RR",
        f"{report['avg_rr']:.2f}",
        "",
        "Best setup",
        report["best_setup"],
        "",
        "Worst setup",
        report["worst_setup"],
        "",
        "Claude usage",
        str(report["claude_requests"]),
        "",
        "API cost",
        f"${report['claude_cost_usd']:.4f}",
        "",
        "Top detected market pattern",
        report["top_pattern"],
    ])
    return msg, report


def build_daily_report_with_experimental_g3(conn: Any) -> tuple[str, dict[str, Any]]:
    msg, report = build_daily_report_g3(conn)
    try:
        from bot.research.market_events.signal_intelligence.experimental_g39 import (
            append_experimental_daily_report_g39,
        )
        return append_experimental_daily_report_g39(conn, msg, report)
    except Exception:
        return msg, report


def maybe_send_daily_report_g3(conn: Any) -> bool:
    """Send once per day after configured local hour."""
    tz = _local_tz()
    now_local = datetime.now(tz)
    if now_local.hour != G3_DAILY_REPORT_HOUR_LOCAL:
        return False

    _, _, report_date = _yesterday_bounds()
    existing = conn.execute(
        "SELECT 1 FROM market_daily_report_g3 WHERE report_date = ? AND telegram_sent = 1",
        (report_date,),
    ).fetchone()
    if existing:
        return False

    msg, report = build_daily_report_with_experimental_g3(conn)
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    sent = False
    if alert_shock_enabled():
        sent = _safe_alert(
            conn,
            event_id=0,
            alert_type=ALERT_SHOCK,
            detail=f"g3-daily-{report_date}",
            message=msg,
            enabled=True,
        )

    existing_row = conn.execute(
        "SELECT id FROM market_daily_report_g3 WHERE report_date = ?",
        (report_date,),
    ).fetchone()
    now = int(time.time())
    if existing_row:
        conn.execute(
            """
            UPDATE market_daily_report_g3
            SET report_json = ?, telegram_sent = ?, created_at = ?
            WHERE report_date = ?
            """,
            (json.dumps(report), 1 if sent else 0, now, report_date),
        )
    else:
        insert_returning_id(
            conn,
            """
            INSERT INTO market_daily_report_g3 (report_date, report_json, telegram_sent, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (report_date, json.dumps(report), 1 if sent else 0, now),
        )
    return sent
