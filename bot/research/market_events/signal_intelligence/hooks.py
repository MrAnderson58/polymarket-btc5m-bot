"""F.0 integration hooks — single entry from paper_runner."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def on_shock_f0(conn: Any, *, event_id: int, force_g2: bool = False) -> None:
    """Research-only F.0 pipeline; never affects paper eligibility."""
    from bot.research.market_events.signal_intelligence.config import F0_ENABLED, F0_AI_ENABLED

    if not F0_ENABLED:
        return

    row = conn.execute("SELECT symbol FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return
    symbol = row["symbol"]

    from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
        STAGE_AI,
        STAGE_F1,
        STAGE_F2,
        STAGE_F3,
        STAGE_F4,
        STAGE_F5,
        STAGE_G1,
        STAGE_F7,
        record_parser_validation_snapshot,
        run_traced,
    )

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

    snap_ok = conn.execute(
        "SELECT 1 FROM market_event_snapshots WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone() is not None
    try:
        record_parser_validation_snapshot(
            conn, event_id=event_id, symbol=symbol, snapshot_ok=snap_ok,
        )
    except Exception as exc:
        logger.debug("f51 early trace skipped: %s", exc)

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
        run_traced(
            conn,
            event_id=event_id,
            stage=STAGE_AI,
            fn=lambda: __import__(
                "bot.research.market_events.signal_intelligence.ai_f0",
                fromlist=["run_f0_ai_analysis"],
            ).run_f0_ai_analysis(conn, event_id),
        )
    else:
        run_traced(conn, event_id=event_id, stage=STAGE_AI, enabled=False, skip_reason="disabled")

    try:
        from bot.research.market_events.signal_intelligence.config import F1_ENABLED
        run_traced(
            conn,
            event_id=event_id,
            stage=STAGE_F1,
            enabled=F1_ENABLED,
            fn=lambda: __import__(
                "bot.research.market_events.signal_intelligence.signal_report_f1",
                fromlist=["run_signal_report_f1"],
            ).run_signal_report_f1(conn, event_id),
        )
    except Exception as exc:
        logger.debug("f1 signal report skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F2_ENABLED
        run_traced(
            conn,
            event_id=event_id,
            stage=STAGE_F2,
            enabled=F2_ENABLED,
            fn=lambda: __import__(
                "bot.research.market_events.signal_intelligence.signal_report_f2",
                fromlist=["run_signal_report_f2"],
            ).run_signal_report_f2(conn, event_id),
        )
    except Exception as exc:
        logger.debug("f2 signal report skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import TREND_SHOCK_ENABLED
        if TREND_SHOCK_ENABLED:
            from bot.research.market_events.signal_intelligence.trend_shock import scan_trend_shock
            scan_trend_shock(conn, symbol=symbol, source_event_id=event_id)
            from bot.research.market_events.signal_intelligence.entry_stages_f3 import run_entry_stage
            run_traced(
                conn,
                event_id=event_id,
                stage=STAGE_F3,
                fn=lambda: run_entry_stage(conn, event_id),
            )
        else:
            run_traced(conn, event_id=event_id, stage=STAGE_F3, enabled=False)
    except Exception as exc:
        logger.debug("f3 entry stage skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import (
            F4_TREND_SHOCK_V2_ENABLED,
            F4_VISUAL_INTEL_ENABLED,
        )
        if F4_TREND_SHOCK_V2_ENABLED or F4_VISUAL_INTEL_ENABLED:
            def _f4() -> None:
                if F4_TREND_SHOCK_V2_ENABLED:
                    from bot.research.market_events.signal_intelligence.trend_shock_v2 import scan_trend_shock_v2
                    scan_trend_shock_v2(conn, symbol=symbol, source_event_id=event_id)
                if F4_VISUAL_INTEL_ENABLED:
                    from bot.research.market_events.signal_intelligence.visual_intel_f4 import run_visual_intel
                    run_visual_intel(conn, event_id)

            run_traced(conn, event_id=event_id, stage=STAGE_F4, fn=_f4)
        else:
            run_traced(conn, event_id=event_id, stage=STAGE_F4, enabled=False)
    except Exception as exc:
        logger.debug("f4 visual intel skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import G1_ENABLED
        run_traced(
            conn,
            event_id=event_id,
            stage=STAGE_G1,
            enabled=G1_ENABLED,
            fn=lambda: __import__(
                "bot.research.market_events.signal_intelligence.liquidity_trend_g1",
                fromlist=["run_liquidity_trend_g1"],
            ).run_liquidity_trend_g1(conn, event_id),
        )
    except Exception as exc:
        logger.debug("g1 liquidity trend skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F5_ENABLED
        run_traced(
            conn,
            event_id=event_id,
            stage=STAGE_F5,
            enabled=F5_ENABLED,
            fn=lambda: __import__(
                "bot.research.market_events.signal_intelligence.professional_signal_f5",
                fromlist=["run_signal_engine_f5"],
            ).run_signal_engine_f5(conn, event_id),
        )
    except Exception as exc:
        logger.debug("f5 signal engine skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F7_ENABLED
        if F7_ENABLED:
            from bot.research.market_events.signal_intelligence.market_intel_f7 import run_f7_pipeline
            run_f7_pipeline(conn, event_id)
        else:
            from bot.research.market_events.signal_intelligence.signal_trace_f51 import record_f7_skipped
            record_f7_skipped(conn, event_id=event_id, reason="disabled")
    except Exception as exc:
        logger.debug("f7 market intel skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import G2_ENABLED
        if G2_ENABLED or force_g2:
            from bot.research.market_events.signal_intelligence.research_g2 import run_research_g2
            run_research_g2(conn, event_id, force=force_g2)
        else:
            from bot.research.market_events.signal_intelligence.signal_trace_f51 import record_g2_skipped
            record_g2_skipped(conn, event_id=event_id, reason="disabled")
    except Exception as exc:
        logger.debug("g2 research agent skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.config import F5_ENABLED, TREND_SHOCK_DEFER_ALERT
        if F5_ENABLED:
            from bot.research.market_events.signal_intelligence.alert_f5 import send_professional_alert_f5
            send_professional_alert_f5(conn, event_id)
        elif TREND_SHOCK_DEFER_ALERT:
            from bot.research.market_events.signal_intelligence.trend_alert_f3 import send_deferred_trend_alert
            send_deferred_trend_alert(conn, event_id)
    except Exception as exc:
        logger.debug("f5/f4 deferred alert skipped: %s", exc)
