"""Opt-in Telegram alerts for market shock / reversal / paper lifecycle."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from bot.research.market_events.alert_config import (
    alert_ai_commentary_enabled,
    alert_chat_id,
    alert_paper_updates_enabled,
    alert_reversal_enabled,
    alert_shock_enabled,
    alerts_enabled,
)
from bot.research.market_events.db import execute_with_retry, insert_returning_id

logger = logging.getLogger(__name__)

ALERT_SHOCK = "SHOCK_DETECTED"
ALERT_REVERSAL = "REVERSAL_CONFIRMED"
ALERT_PAPER = "PAPER_POSITION_UPDATE"
ALERT_AI = "AI_RESEARCH_NOTE"

PAPER_LABEL = "PAPER — research only, no live order"


def _col(row: Any, name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def _dedupe_key(event_id: int, alert_type: str, detail: str = "") -> str:
    return f"{event_id}:{alert_type}:{detail}"


def _already_sent(conn: Any, dedupe_key: str) -> bool:
    row = conn.execute(
        "SELECT id FROM market_event_alert_log WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    return row is not None


def _send_telegram(
    text: str,
    *,
    conn: Any | None = None,
    alert_type: str = "UNKNOWN",
    event_id: int = 0,
) -> tuple[bool, str | None]:
    from bot.research.market_events.alert_engine.telegram_delivery import deliver_telegram

    result = deliver_telegram(
        text, conn=conn, alert_type=alert_type, event_id=event_id,
    )
    return result.ok, result.error


def _record_alert(
    conn: Any,
    *,
    event_id: int,
    alert_type: str,
    dedupe_key: str,
    message_text: str,
    sent: bool,
    latency_ms: float,
    error: str | None = None,
) -> None:
    execute_with_retry(
        conn,
        """
        INSERT OR IGNORE INTO market_event_alert_log (
          event_id, alert_type, dedupe_key, message_text, sent, latency_ms,
          error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, alert_type, dedupe_key, message_text,
            1 if sent else 0, latency_ms, error, int(time.time()),
        ),
    )


def _safe_alert(
    conn: Any,
    *,
    event_id: int,
    alert_type: str,
    detail: str,
    message: str,
    enabled: bool,
) -> bool:
    """Send alert with dedupe; never raises."""
    if not alerts_enabled() or not enabled:
        return False
    key = _dedupe_key(event_id, alert_type, detail)
    if _already_sent(conn, key):
        return False
    t0 = time.perf_counter()
    sent, err = False, None
    try:
        sent, err = _send_telegram(
            message, conn=conn, alert_type=alert_type, event_id=event_id,
        )
    except Exception as exc:
        err = str(exc)
        logger.warning("alert %s event=%s failed: %s", alert_type, event_id, exc)
    latency = (time.perf_counter() - t0) * 1000.0
    try:
        _record_alert(
            conn, event_id=event_id, alert_type=alert_type, dedupe_key=key,
            message_text=message, sent=sent, latency_ms=latency, error=err,
        )
    except Exception as exc:
        logger.warning("alert log persist failed: %s", exc)
    return sent


def format_shock_alert(conn: Any, event_id: int) -> str:
    from bot.research.market_events.alert_engine.format_v2 import format_shock_alert_v2
    return format_shock_alert_v2(conn, event_id)


def alert_shock_detected(conn: Any, event_id: int) -> bool:
    msg = format_shock_alert(conn, event_id)
    return _safe_alert(
        conn, event_id=event_id, alert_type=ALERT_SHOCK, detail="",
        message=msg, enabled=alert_shock_enabled(),
    )


def format_reversal_alert(
    conn: Any,
    *,
    event_id: int,
    reversal_variant: str,
    confirm_latency_sec: int | None,
    extreme_price: float | None,
    path_json: dict | None,
    paper_runs: int,
) -> str:
    from bot.research.market_events.alert_engine.format_v2 import format_reversal_alert_v2
    return format_reversal_alert_v2(
        conn,
        event_id=event_id,
        reversal_variant=reversal_variant,
        confirm_latency_sec=confirm_latency_sec,
        extreme_price=extreme_price,
        path_json=path_json,
        paper_runs=paper_runs,
    )


def alert_reversal_confirmed(
    conn: Any,
    *,
    event_id: int,
    reversal_variant: str,
    confirm_latency_sec: int | None = None,
    extreme_price: float | None = None,
    path_json: dict | None = None,
    paper_runs: int = 5,
) -> bool:
    msg = format_reversal_alert(
        conn, event_id=event_id, reversal_variant=reversal_variant,
        confirm_latency_sec=confirm_latency_sec, extreme_price=extreme_price,
        path_json=path_json, paper_runs=paper_runs,
    )
    return _safe_alert(
        conn, event_id=event_id, alert_type=ALERT_REVERSAL,
        detail=reversal_variant, message=msg, enabled=alert_reversal_enabled(),
    )


def alert_paper_position_update(
    conn: Any,
    *,
    event_id: int,
    update_type: str,
    symbol: str,
    reversal_variant: str,
    exit_variant: str,
    detail: str = "",
) -> bool:
    if not alert_paper_updates_enabled():
        return False
    if update_type in ("TP", "STOP", "BE_STOP", "CLOSED"):
        from bot.research.market_events.alert_engine.format_v2 import format_paper_result_v2
        msg = format_paper_result_v2(
            conn,
            event_id=event_id,
            symbol=symbol,
            reversal_variant=reversal_variant,
            exit_variant=exit_variant,
            update_type=update_type if update_type != "BE_STOP" else "STOP",
            detail=detail,
        )
    else:
        msg = "\n".join([
            f"PAPER POSITION UPDATE — {PAPER_LABEL}",
            "",
            f"event_id: {event_id}  symbol: {symbol}",
            f"update: {update_type}",
            f"strategy: REVERSAL_{reversal_variant}_{exit_variant}",
            detail,
        ])
    dedupe_detail = f"{update_type}:{reversal_variant}:{exit_variant}"
    return _safe_alert(
        conn, event_id=event_id, alert_type=ALERT_PAPER,
        detail=dedupe_detail, message=msg, enabled=True,
    )


def alert_ai_research_note(conn: Any, event_id: int, commentary: str) -> bool:
    if not alert_ai_commentary_enabled():
        return False
    msg = commentary
    return _safe_alert(
        conn, event_id=event_id, alert_type=ALERT_AI, detail="shadow",
        message=msg, enabled=True,
    )
