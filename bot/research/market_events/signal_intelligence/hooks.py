"""F.0 integration hooks — single entry from paper_runner."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def on_shock_f0(conn: Any, *, event_id: int) -> None:
    """Research-only F.0 pipeline; never affects paper eligibility."""
    from bot.research.market_events.signal_intelligence.config import F0_ENABLED, F0_AI_ENABLED

    if not F0_ENABLED:
        return

    row = conn.execute("SELECT symbol FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return
    symbol = row["symbol"]

    try:
        from bot.research.market_events.signal_intelligence.exchange_resolver import get_or_resolve
        get_or_resolve(conn, symbol)
    except Exception as exc:
        logger.debug("f0 resolver skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.exchange_context import capture_exchange_context
        capture_exchange_context(conn, event_id=event_id, symbol=symbol)
    except Exception as exc:
        logger.debug("f0 exchange context skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.multitimeframe import scan_multitimeframe
        scan_multitimeframe(conn, symbol=symbol, source_event_id=event_id)
    except Exception as exc:
        logger.debug("f0 mtf skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.exhaustion import scan_exhaustion
        scan_exhaustion(conn, symbol=symbol, source_event_id=event_id)
    except Exception as exc:
        logger.debug("f0 exhaustion skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.opportunity_v2 import persist_opportunity_score_v2
        persist_opportunity_score_v2(conn, event_id=event_id)
    except Exception as exc:
        logger.debug("f0 opportunity v2 skipped: %s", exc)

    if F0_AI_ENABLED:
        try:
            from bot.research.market_events.signal_intelligence.ai_f0 import run_f0_ai_analysis
            run_f0_ai_analysis(conn, event_id)
        except Exception as exc:
            logger.debug("f0 ai skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F1_ENABLED
        if F1_ENABLED:
            from bot.research.market_events.signal_intelligence.signal_report_f1 import run_signal_report_f1
            run_signal_report_f1(conn, event_id)
    except Exception as exc:
        logger.debug("f1 signal report skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F2_ENABLED
        if F2_ENABLED:
            from bot.research.market_events.signal_intelligence.signal_report_f2 import run_signal_report_f2
            run_signal_report_f2(conn, event_id)
    except Exception as exc:
        logger.debug("f2 signal report skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import TREND_SHOCK_ENABLED
        if TREND_SHOCK_ENABLED:
            from bot.research.market_events.signal_intelligence.trend_shock import scan_trend_shock
            scan_trend_shock(conn, symbol=symbol, source_event_id=event_id)
    except Exception as exc:
        logger.debug("f3 trend shock skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import TREND_SHOCK_ENABLED
        if TREND_SHOCK_ENABLED:
            from bot.research.market_events.signal_intelligence.entry_stages_f3 import run_entry_stage
            run_entry_stage(conn, event_id)
    except Exception as exc:
        logger.debug("f3 entry stage skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F4_TREND_SHOCK_V2_ENABLED
        if F4_TREND_SHOCK_V2_ENABLED:
            from bot.research.market_events.signal_intelligence.trend_shock_v2 import scan_trend_shock_v2
            scan_trend_shock_v2(conn, symbol=symbol, source_event_id=event_id)
    except Exception as exc:
        logger.debug("f4 trend shock v2 skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F4_VISUAL_INTEL_ENABLED
        if F4_VISUAL_INTEL_ENABLED:
            from bot.research.market_events.signal_intelligence.visual_intel_f4 import run_visual_intel
            run_visual_intel(conn, event_id)
    except Exception as exc:
        logger.debug("f4 visual intel skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import TREND_SHOCK_DEFER_ALERT
        if TREND_SHOCK_DEFER_ALERT:
            from bot.research.market_events.signal_intelligence.trend_alert_f3 import send_deferred_trend_alert
            send_deferred_trend_alert(conn, event_id)
    except Exception as exc:
        logger.debug("f4 deferred alert skipped: %s", exc)
