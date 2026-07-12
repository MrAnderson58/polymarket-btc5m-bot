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
    message_type: str | None = None,
    telegram_message_stage: str = "NONE",
    duplicate_prevented: int = 0,
) -> None:
    execute_with_retry(
        conn,
        """
        INSERT OR IGNORE INTO market_event_alert_log (
          event_id, alert_type, dedupe_key, message_text, sent, latency_ms,
          error, created_at, message_type, telegram_message_stage, duplicate_prevented
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, alert_type, dedupe_key, message_text,
            1 if sent else 0, latency_ms, error, int(time.time()),
            message_type, telegram_message_stage, duplicate_prevented,
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
    message_type: str | None = None,
) -> bool:
    """Send alert with dedupe; never raises."""
    from bot.research.market_events.signal_intelligence.config import F41_TELEGRAM_DEDUPE

    if not alerts_enabled() or not enabled:
        return False
    key = _dedupe_key(event_id, alert_type, detail)
    stage = "NONE"
    resolved_message_type: str | None = None

    if F41_TELEGRAM_DEDUPE and message_type:
        from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import (
            STAGE_FOR_MESSAGE_TYPE,
            STAGE_NONE,
            merge_ai_into_shock_message,
            MSG_SHOCK,
            record_duplicate_prevented,
            stage_already_sent,
        )

        resolved_message_type = message_type
        if stage_already_sent(conn, event_id, message_type):
            try:
                record_duplicate_prevented(
                    conn,
                    event_id=event_id,
                    alert_type=alert_type,
                    message_type=message_type,
                    dedupe_key=key,
                )
            except Exception as exc:
                logger.warning("duplicate prevented log failed: %s", exc)
            return False
        if _already_sent(conn, key):
            try:
                record_duplicate_prevented(
                    conn,
                    event_id=event_id,
                    alert_type=alert_type,
                    message_type=message_type,
                    dedupe_key=f"{key}:dedupe",
                )
            except Exception as exc:
                logger.warning("duplicate prevented log failed: %s", exc)
            return False
        if message_type == MSG_SHOCK:
            message = merge_ai_into_shock_message(conn, event_id, message)
    elif _already_sent(conn, key):
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
    if F41_TELEGRAM_DEDUPE and resolved_message_type and sent:
        from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import (
            STAGE_FOR_MESSAGE_TYPE,
            STAGE_NONE,
        )
        stage = STAGE_FOR_MESSAGE_TYPE.get(resolved_message_type, STAGE_NONE)
    try:
        _record_alert(
            conn, event_id=event_id, alert_type=alert_type, dedupe_key=key,
            message_text=message, sent=sent, latency_ms=latency, error=err,
            message_type=resolved_message_type, telegram_message_stage=stage,
        )
    except Exception as exc:
        logger.warning("alert log persist failed: %s", exc)
    return sent


def format_shock_alert(conn: Any, event_id: int) -> str:
    from bot.research.market_events.signal_intelligence.config import F5_TELEGRAM_FORMAT
    if F5_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            from bot.research.market_events.signal_intelligence.telegram_f5 import format_shock_f5
            return format_shock_f5(conn, event_id)
    from bot.research.market_events.signal_intelligence.config import F4_TELEGRAM_FORMAT
    if F4_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            from bot.research.market_events.signal_intelligence.telegram_f4 import render_final_telegram_f4
            return render_final_telegram_f4(conn, event_id)
    from bot.research.market_events.signal_intelligence.config import TREND_PREMIUM_TELEGRAM
    if TREND_PREMIUM_TELEGRAM:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            from bot.research.market_events.signal_intelligence.telegram_trend_premium import (
                render_trend_premium_v2,
            )
            return render_trend_premium_v2(conn, event_id)
    from bot.research.market_events.signal_intelligence.config import F3_TELEGRAM_FORMAT
    if F3_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            from bot.research.market_events.signal_intelligence.telegram_f3 import format_shock_f3
            return format_shock_f3(conn, event_id)
    from bot.research.market_events.signal_intelligence.config import F2_TELEGRAM_FORMAT
    if F2_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            from bot.research.market_events.signal_intelligence.telegram_f2 import format_shock_f2
            return format_shock_f2(conn, event_id)
    from bot.research.market_events.signal_intelligence.config import F1_TELEGRAM_FORMAT
    if F1_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f1 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            from bot.research.market_events.signal_intelligence.telegram_f1 import format_shock_f1
            return format_shock_f1(conn, event_id)
    from bot.research.market_events.signal_intelligence.config import F0_TELEGRAM_FORMAT
    if F0_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_opportunity_scores_v2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            from bot.research.market_events.signal_intelligence.telegram_f0 import format_shock_f0
            return format_shock_f0(conn, event_id)
    from bot.research.market_events.alert_engine.format_v2 import format_shock_alert_v2
    return format_shock_alert_v2(conn, event_id)


def alert_shock_detected(conn: Any, event_id: int) -> bool:
    from bot.research.market_events.signal_intelligence.config import (
        F41_TELEGRAM_DEDUPE,
        F5_ENABLED,
        TREND_SHOCK_DEFER_ALERT,
    )
    from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import MSG_SHOCK
    if TREND_SHOCK_DEFER_ALERT or F5_ENABLED:
        return False
    msg = format_shock_alert(conn, event_id)
    return _safe_alert(
        conn, event_id=event_id, alert_type=ALERT_SHOCK, detail="",
        message=msg, enabled=alert_shock_enabled(),
        message_type=MSG_SHOCK if F41_TELEGRAM_DEDUPE else None,
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

    from bot.research.market_events.signal_intelligence.config import F41_TELEGRAM_DEDUPE
    from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import (
        message_type_for_paper_update,
    )

    paper_message_type = message_type_for_paper_update(update_type) if F41_TELEGRAM_DEDUPE else None

    from bot.research.market_events.signal_intelligence.config import F3_TELEGRAM_FORMAT, F2_TELEGRAM_FORMAT, F1_TELEGRAM_FORMAT

    if update_type == "PAPER_ENTRY" and F3_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            run = conn.execute(
                """
                SELECT entry_price, initial_stop FROM paper_strategy_runs
                WHERE event_id = ? AND reversal_variant = ? LIMIT 1
                """,
                (event_id, reversal_variant),
            ).fetchone()
            entry = float(run["entry_price"]) if run and run["entry_price"] else 0.0
            stop = float(run["initial_stop"]) if run and run["initial_stop"] else 0.0
            report = conn.execute(
                "SELECT expected_target_pct FROM market_events_signal_reports_f2 WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            target = entry
            if report and entry:
                me = conn.execute("SELECT direction FROM market_events WHERE id = ?", (event_id,)).fetchone()
                tp = float(report["expected_target_pct"]) / 100.0
                if me and me["direction"] == "UP":
                    target = entry * (1 - tp)
                else:
                    target = entry * (1 + tp)
            from bot.research.market_events.signal_intelligence.telegram_f3 import format_entry_f3
            msg = format_entry_f3(
                conn, event_id=event_id, symbol=symbol,
                reversal_variant=reversal_variant, entry=entry, stop=stop, target=target,
            )
            dedupe_detail = f"{update_type}:{reversal_variant}:{exit_variant}"
            return _safe_alert(
                conn, event_id=event_id, alert_type=ALERT_PAPER,
                detail=dedupe_detail, message=msg, enabled=True,
                message_type=paper_message_type,
            )

    if update_type == "PAPER_ENTRY" and F2_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            run = conn.execute(
                """
                SELECT entry_price, initial_stop FROM paper_strategy_runs
                WHERE event_id = ? AND reversal_variant = ? LIMIT 1
                """,
                (event_id, reversal_variant),
            ).fetchone()
            entry = float(run["entry_price"]) if run and run["entry_price"] else 0.0
            stop = float(run["initial_stop"]) if run and run["initial_stop"] else 0.0
            report = conn.execute(
                "SELECT expected_target_pct FROM market_events_signal_reports_f2 WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            target = entry
            if report and entry:
                me = conn.execute("SELECT direction FROM market_events WHERE id = ?", (event_id,)).fetchone()
                tp = float(report["expected_target_pct"]) / 100.0
                if me and me["direction"] == "UP":
                    target = entry * (1 - tp)
                else:
                    target = entry * (1 + tp)
            from bot.research.market_events.signal_intelligence.telegram_f2 import format_entry_f2
            msg = format_entry_f2(
                conn, event_id=event_id, symbol=symbol,
                reversal_variant=reversal_variant, entry=entry, stop=stop, target=target,
            )
            dedupe_detail = f"{update_type}:{reversal_variant}:{exit_variant}"
            return _safe_alert(
                conn, event_id=event_id, alert_type=ALERT_PAPER,
                detail=dedupe_detail, message=msg, enabled=True,
                message_type=paper_message_type,
            )

    if update_type == "PAPER_ENTRY" and F1_TELEGRAM_FORMAT:
        row = conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f1 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row:
            run = conn.execute(
                """
                SELECT entry_price, initial_stop FROM paper_strategy_runs
                WHERE event_id = ? AND reversal_variant = ? LIMIT 1
                """,
                (event_id, reversal_variant),
            ).fetchone()
            entry = float(run["entry_price"]) if run and run["entry_price"] else 0.0
            stop = float(run["initial_stop"]) if run and run["initial_stop"] else 0.0
            report = conn.execute(
                "SELECT expected_target_pct FROM market_events_signal_reports_f1 WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            target = entry
            if report and entry:
                me = conn.execute("SELECT direction FROM market_events WHERE id = ?", (event_id,)).fetchone()
                tp = float(report["expected_target_pct"]) / 100.0
                if me and me["direction"] == "UP":
                    target = entry * (1 - tp)
                else:
                    target = entry * (1 + tp)
            from bot.research.market_events.signal_intelligence.telegram_f1 import format_entry_f1
            msg = format_entry_f1(
                conn, event_id=event_id, symbol=symbol,
                reversal_variant=reversal_variant, entry=entry, stop=stop, target=target,
            )
            dedupe_detail = f"{update_type}:{reversal_variant}:{exit_variant}"
            return _safe_alert(
                conn, event_id=event_id, alert_type=ALERT_PAPER,
                detail=dedupe_detail, message=msg, enabled=True,
                message_type=paper_message_type,
            )

    if update_type in ("TP", "STOP", "BE_STOP", "CLOSED"):
        if F3_TELEGRAM_FORMAT and conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone():
            run = conn.execute(
                """
                SELECT id, net_return, gross_return, duration_seconds
                FROM paper_strategy_runs
                WHERE event_id = ? AND reversal_variant = ? AND exit_variant = ?
                """,
                (event_id, reversal_variant, exit_variant),
            ).fetchone()
            pnl = float(run["net_return"] or run["gross_return"] or 0) if run else 0.0
            dur = int(run["duration_seconds"] or 0) if run else 0
            holding_min = max(1, dur // 60)
            try:
                from bot.research.market_events.signal_intelligence.outcome_f1 import record_outcome_f1
                record_outcome_f1(
                    conn,
                    event_id=event_id,
                    paper_run_id=int(run["id"]) if run else None,
                    pnl_pct=pnl,
                    holding_seconds=dur,
                )
            except Exception:
                pass
            from bot.research.market_events.signal_intelligence.outcome_f1 import _ai_agreed
            from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2
            report = load_signal_report_f2(conn, event_id)
            hist_matched = None
            if report:
                hist_matched = pnl >= report.expected_target_pct * 0.5
            from bot.research.market_events.signal_intelligence.telegram_f3 import format_result_f3
            reason = update_type if update_type != "BE_STOP" else "STOP"
            msg = format_result_f3(
                conn,
                event_id=event_id,
                symbol=symbol,
                pnl_pct=pnl,
                holding_min=holding_min,
                exit_variant=reason if reason in ("TP", "STOP") else exit_variant,
                ai_agreed=_ai_agreed(conn, event_id, pnl),
                hist_matched=hist_matched,
            )
        elif F2_TELEGRAM_FORMAT and conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f2 WHERE event_id = ?",
            (event_id,),
        ).fetchone():
            run = conn.execute(
                """
                SELECT id, net_return, gross_return, duration_seconds
                FROM paper_strategy_runs
                WHERE event_id = ? AND reversal_variant = ? AND exit_variant = ?
                """,
                (event_id, reversal_variant, exit_variant),
            ).fetchone()
            pnl = float(run["net_return"] or run["gross_return"] or 0) if run else 0.0
            dur = int(run["duration_seconds"] or 0) if run else 0
            holding_min = max(1, dur // 60)
            try:
                from bot.research.market_events.signal_intelligence.outcome_f1 import record_outcome_f1
                record_outcome_f1(
                    conn,
                    event_id=event_id,
                    paper_run_id=int(run["id"]) if run else None,
                    pnl_pct=pnl,
                    holding_seconds=dur,
                )
            except Exception:
                pass
            from bot.research.market_events.signal_intelligence.outcome_f1 import _ai_agreed
            from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2
            report = load_signal_report_f2(conn, event_id)
            hist_matched = None
            if report:
                hist_matched = pnl >= report.expected_target_pct * 0.5
            from bot.research.market_events.signal_intelligence.telegram_f2 import format_result_f2
            reason = update_type if update_type != "BE_STOP" else "STOP"
            msg = format_result_f2(
                conn,
                event_id=event_id,
                symbol=symbol,
                pnl_pct=pnl,
                holding_min=holding_min,
                exit_variant=reason if reason in ("TP", "STOP") else exit_variant,
                ai_agreed=_ai_agreed(conn, event_id, pnl),
                hist_matched=hist_matched,
            )
        elif F1_TELEGRAM_FORMAT and conn.execute(
            "SELECT 1 FROM market_events_signal_reports_f1 WHERE event_id = ?",
            (event_id,),
        ).fetchone():
            run = conn.execute(
                """
                SELECT id, net_return, gross_return, duration_seconds
                FROM paper_strategy_runs
                WHERE event_id = ? AND reversal_variant = ? AND exit_variant = ?
                """,
                (event_id, reversal_variant, exit_variant),
            ).fetchone()
            pnl = float(run["net_return"] or run["gross_return"] or 0) if run else 0.0
            dur = int(run["duration_seconds"] or 0) if run else 0
            holding_min = max(1, dur // 60)
            try:
                from bot.research.market_events.signal_intelligence.outcome_f1 import record_outcome_f1
                record_outcome_f1(
                    conn,
                    event_id=event_id,
                    paper_run_id=int(run["id"]) if run else None,
                    pnl_pct=pnl,
                    holding_seconds=dur,
                )
            except Exception:
                pass
            from bot.research.market_events.signal_intelligence.outcome_f1 import _ai_agreed
            from bot.research.market_events.signal_intelligence.signal_report_f1 import load_signal_report_f1
            report = load_signal_report_f1(conn, event_id)
            hist_matched = None
            if report:
                hist_matched = pnl >= report.expected_target_pct * 0.5
            from bot.research.market_events.signal_intelligence.telegram_f1 import format_result_f1
            reason = update_type if update_type != "BE_STOP" else "STOP"
            msg = format_result_f1(
                conn,
                event_id=event_id,
                symbol=symbol,
                pnl_pct=pnl,
                holding_min=holding_min,
                exit_variant=reason if reason in ("TP", "STOP") else exit_variant,
                ai_agreed=_ai_agreed(conn, event_id, pnl),
                hist_matched=hist_matched,
            )
        else:
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
        message_type=paper_message_type,
    )


def alert_ai_research_note(conn: Any, event_id: int, commentary: str) -> bool:
    from bot.research.market_events.signal_intelligence.config import F41_TELEGRAM_DEDUPE
    if F41_TELEGRAM_DEDUPE:
        logger.debug(
            "ai research note suppressed (merged into shock alert) event=%s len=%s",
            event_id, len(commentary or ""),
        )
        return False
    if not alert_ai_commentary_enabled():
        return False
    return _safe_alert(
        conn, event_id=event_id, alert_type=ALERT_AI, detail="shadow",
        message=commentary, enabled=True,
    )
