"""Phase F.5 — gated professional Telegram alert sender."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def send_professional_alert_f5(conn: Any, event_id: int) -> bool:
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert
    from bot.research.market_events.signal_intelligence.config import (
        F41_TELEGRAM_DEDUPE,
        F5_ENABLED,
        F5_PRIORITY_ENGINE,
        F5_TELEGRAM_FORMAT,
        F7_ENABLED,
        F7_TELEGRAM_FORMAT,
    )
    from bot.research.market_events.signal_intelligence.priority_engine_f5 import mark_f5_telegram_sent
    from bot.research.market_events.signal_intelligence.professional_signal_f5 import (
        load_professional_signal_f5,
        run_signal_engine_f5,
    )
    from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
        _linked_telegram,
        record_f5_delivery_trace,
    )
    from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import MSG_SHOCK
    from bot.research.market_events.signal_intelligence.telegram_f5 import format_shock_f5

    if not F5_ENABLED or not alert_shock_enabled():
        return False

    g3_row = conn.execute(
        """
        SELECT telegram_rendered, telegram_sent, dashboard_only
        FROM market_live_signals_g3 WHERE event_id = ? ORDER BY id DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if g3_row and g3_row["telegram_sent"] and not g3_row["dashboard_only"]:
        logger.debug("f5 alert skipped — G3 telegram already sent event=%s", event_id)
        return True
    if g3_row and g3_row["telegram_rendered"] and not g3_row["telegram_sent"] and not g3_row["dashboard_only"]:
        dedupe_key = f"g3-event-{event_id}"
        ok = _safe_alert(
            conn, event_id=event_id, alert_type=ALERT_SHOCK, detail=dedupe_key,
            message=str(g3_row["telegram_rendered"]), enabled=True,
        )
        if ok:
            conn.execute(
                "UPDATE market_live_signals_g3 SET telegram_sent = 1 WHERE event_id = ?",
                (event_id,),
            )
        record_f5_delivery_trace(
            conn, event_id=event_id, message_id=None,
            dynamic_confidence=0.0, telegram_eligible=True,
            telegram_skip_reason=None, telegram_sent=ok,
        )
        return ok

    signal = load_professional_signal_f5(conn, event_id)
    if not signal:
        signal = run_signal_engine_f5(conn, event_id)
    if not signal:
        logger.debug("f5 alert skipped — no signal event=%s", event_id)
        return False

    message_id, _ = _linked_telegram(conn, event_id)
    telegram_sent = False

    if F5_PRIORITY_ENGINE and not signal.telegram_eligible:
        mark_f5_telegram_sent(conn, event_id, skipped_reason=signal.telegram_skip_reason)
        logger.debug(
            "f5 alert dashboard-only event=%s reason=%s conf=%.1f",
            event_id, signal.telegram_skip_reason, signal.dynamic_confidence,
        )
        try:
            from bot.research.market_events.signal_intelligence.near_miss_f73 import (
                record_near_miss_f73,
            )
            record_near_miss_f73(
                conn, event_id=event_id,
                skip_code=signal.telegram_skip_reason or "low_confidence",
                signal=signal,
            )
        except Exception as exc:
            logger.debug("f73 near miss skipped: %s", exc)
        record_f5_delivery_trace(
            conn,
            event_id=event_id,
            message_id=message_id,
            dynamic_confidence=signal.dynamic_confidence,
            telegram_eligible=False,
            telegram_skip_reason=signal.telegram_skip_reason,
            telegram_sent=False,
        )
        return False

    f7_intel = None
    if F7_ENABLED:
        from bot.research.market_events.signal_intelligence.market_intel_f7 import (
            load_market_intelligence_f7,
            run_market_intel_f7,
        )
        f7_intel = load_market_intelligence_f7(conn, event_id)
        if not f7_intel:
            f7_intel = run_market_intel_f7(conn, event_id)
        if f7_intel and not f7_intel.telegram_eligible:
            mark_f5_telegram_sent(conn, event_id, skipped_reason=f7_intel.telegram_skip_reason)
            try:
                from bot.research.market_events.signal_intelligence.near_miss_f73 import (
                    record_near_miss_f73,
                )
                record_near_miss_f73(
                    conn, event_id=event_id,
                    skip_code=f7_intel.telegram_skip_reason or "f5_filtered",
                    signal=signal, f7_intel=f7_intel,
                )
            except Exception as exc:
                logger.debug("f73 near miss skipped: %s", exc)
            record_f5_delivery_trace(
                conn,
                event_id=event_id,
                message_id=message_id,
                dynamic_confidence=f7_intel.final_confidence,
                telegram_eligible=False,
                telegram_skip_reason=f7_intel.telegram_skip_reason,
                telegram_sent=False,
            )
            return False

    if F7_ENABLED and F7_TELEGRAM_FORMAT and f7_intel:
        msg = f7_intel.telegram_rendered
        try:
            from bot.research.market_events.signal_intelligence.config import G2_TELEGRAM_FORMAT
            if G2_TELEGRAM_FORMAT:
                from bot.research.market_events.signal_intelligence.research_g2 import load_research_g2
                g2 = load_research_g2(conn, event_id)
                if g2 and g2.telegram_block and g2.telegram_block not in msg:
                    msg = msg.rstrip() + "\n\n" + g2.telegram_block
        except Exception:
            pass
    elif F5_TELEGRAM_FORMAT:
        msg = format_shock_f5(conn, event_id)
    else:
        msg = signal.telegram_rendered
    sent = _safe_alert(
        conn,
        event_id=event_id,
        alert_type=ALERT_SHOCK,
        detail="professional_f5",
        message=msg,
        enabled=True,
        message_type=MSG_SHOCK if F41_TELEGRAM_DEDUPE else None,
    )
    if sent:
        mark_f5_telegram_sent(conn, event_id)
        telegram_sent = True
        try:
            from bot.research.market_events.signal_intelligence.signal_outcome_f72 import (
                create_active_signal_f72,
            )
            create_active_signal_f72(conn, event_id=event_id)
        except Exception as exc:
            logger.debug("f72 active signal skipped: %s", exc)
    elif F5_PRIORITY_ENGINE:
        mark_f5_telegram_sent(conn, event_id, skipped_reason="send_failed")
        try:
            from bot.research.market_events.signal_intelligence.near_miss_f73 import (
                record_near_miss_f73,
            )
            record_near_miss_f73(conn, event_id=event_id, skip_code="send_failed", signal=signal)
        except Exception as exc:
            logger.debug("f73 near miss skipped: %s", exc)

    record_f5_delivery_trace(
        conn,
        event_id=event_id,
        message_id=message_id,
        dynamic_confidence=(
            f7_intel.final_confidence if f7_intel else signal.dynamic_confidence
        ),
        telegram_eligible=signal.telegram_eligible,
        telegram_skip_reason=signal.telegram_skip_reason if not telegram_sent else None,
        telegram_sent=telegram_sent,
    )
    return sent
