"""Phase G.3.2 — morning Telegram digest of missed profitable candidates."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.research.market_events.signal_intelligence.config import (
    G3_DAILY_REPORT_HOUR_LOCAL,
    G3_MIN_CONFIDENCE,
    G3_MIN_MARKET_SCORE,
    G32_MISSED_TOP_N,
)

_DEFAULT_TZ = "Europe/Moscow"


def _local_tz() -> ZoneInfo:
    import os
    name = os.getenv("ME_G3_REPORT_TZ", _DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _yesterday_bounds() -> tuple[int, int]:
    tz = _local_tz()
    now_local = datetime.now(tz)
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_local.hour < G3_DAILY_REPORT_HOUR_LOCAL:
        start = start.fromtimestamp(start.timestamp() - 86400, tz=tz)
    day_start = int(start.astimezone(timezone.utc).timestamp()) - 86400
    day_end = day_start + 86400 - 1
    return day_start, day_end


def fetch_missed_opportunities_g32(conn: Any, *, limit: int | None = None) -> list[Any]:
    day_start, day_end = _yesterday_bounds()
    n = limit if limit is not None else G32_MISSED_TOP_N
    return conn.execute(
        """
        SELECT o.symbol, o.max_profit_pct, c.confidence, c.market_score,
               c.rejection_reason, c.candidate_state
        FROM market_candidate_outcomes_g32 o
        JOIN market_candidate_g31 c ON c.id = o.candidate_id
        WHERE o.created_at BETWEEN ? AND ?
          AND c.candidate_state != 'accepted'
          AND o.max_profit_pct >= 1.5
        ORDER BY o.max_profit_pct DESC
        LIMIT ?
        """,
        (day_start, day_end, n),
    ).fetchall()


def _format_rejection_with_threshold(reason: str | None, *, conf: float | None, mscore: float | None) -> str:
    if not reason:
        return "—"
    if reason.startswith("Confidence") and conf is not None:
        return f"{reason} (порог {G3_MIN_CONFIDENCE})"
    if reason.startswith("Market Score") and mscore is not None:
        return f"{reason} (порог {G3_MIN_MARKET_SCORE:.0f})"
    return reason


def build_missed_opportunities_telegram_g32(conn: Any) -> str | None:
    rows = fetch_missed_opportunities_g32(conn)
    if not rows:
        return None

    lines = ["Сегодня были упущены", ""]
    for r in rows:
        lines.extend([
            str(r["symbol"]),
            "",
            f"+{float(r['max_profit_pct']):+.1f}%",
            "",
            "Причина отказа",
            "",
            _format_rejection_with_threshold(
                r["rejection_reason"],
                conf=float(r["confidence"]) if r["confidence"] is not None else None,
                mscore=float(r["market_score"]) if r["market_score"] is not None else None,
            ),
            "",
        ])
    return "\n".join(lines).rstrip()


def maybe_send_missed_opportunities_g32(conn: Any) -> bool:
    """Send once per morning with daily report."""
    tz = _local_tz()
    now_local = datetime.now(tz)
    if now_local.hour != G3_DAILY_REPORT_HOUR_LOCAL:
        return False

    day_start, _ = _yesterday_bounds()
    report_date = datetime.fromtimestamp(day_start, tz=tz).strftime("%Y-%m-%d")

    existing = conn.execute(
        "SELECT value FROM market_events_g3_ops_state WHERE key = ?",
        (f"g32_missed_sent_{report_date}",),
    ).fetchone()
    if existing:
        return False

    msg = build_missed_opportunities_telegram_g32(conn)
    if not msg:
        return False

    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import _safe_alert

    sent = False
    if alert_shock_enabled():
        sent = _safe_alert(
            conn,
            event_id=0,
            alert_type="HEARTBEAT",
            detail=f"g32-missed-{report_date}",
            message=msg,
            enabled=True,
        )

    if sent:
        from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state
        set_g3_ops_state(conn, f"g32_missed_sent_{report_date}", "1")

    return sent
