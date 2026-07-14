"""Phase G.3 — background runner (recorder + detector + signals + follow-up)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.config import (
    G3_ENABLED,
    G3_RECORDER_INTERVAL_SEC,
)

logger = logging.getLogger(__name__)


@dataclass
class G3CycleStats:
    cycles: int = 0
    snapshots: int = 0
    trends: int = 0
    candidates: int = 0
    signals: int = 0
    followups: int = 0
    replay_updates: int = 0
    errors: int = 0
    last_snapshot_id: int | None = None
    error_messages: list[str] = field(default_factory=list)


def run_g3_cycle(conn, *, provider=None) -> G3CycleStats:
    """Single G3 production cycle."""
    from bot.research.market_events.signal_intelligence.daily_report_g3 import maybe_send_daily_report_g3
    from bot.research.market_events.signal_intelligence.followup_g3 import check_signal_followups_g3
    from bot.research.market_events.signal_intelligence.health_g3 import update_recorder_health_g3
    from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import (
        compute_liquidity_state_g3,
        persist_liquidity_state_g3,
    )
    from bot.research.market_events.signal_intelligence.recorder_g3 import (
        purge_old_snapshots_g3,
        record_market_snapshot_g3,
    )
    from bot.research.market_events.signal_intelligence.signal_generator_g3 import (
        evaluate_live_signal_g3,
        send_live_signal_telegram_g3,
    )
    from bot.research.market_events.signal_intelligence.trend_windows_g3 import run_trend_detection_g3

    stats = G3CycleStats(cycles=1)
    try:
        from bot.research.market_events.signal_intelligence.candidate_g31 import (
            load_g31_universe_symbols,
            run_candidate_pipeline_g31,
        )

        snapshot_id, payload = record_market_snapshot_g3(conn, provider=provider)
        stats.snapshots = 1
        stats.last_snapshot_id = snapshot_id
        update_recorder_health_g3(
            conn,
            snapshot_id=snapshot_id,
            latency_ms=payload.collector_latency_ms,
            status=payload.recorder_status,
        )
        purge_old_snapshots_g3(conn)

        universe = load_g31_universe_symbols(conn)
        trends = run_trend_detection_g3(conn, snapshot_id=snapshot_id, symbols=universe)
        stats.trends = len(trends)

        liquidity = compute_liquidity_state_g3(conn, snapshot_id=snapshot_id)
        persist_liquidity_state_g3(conn, snapshot_id=snapshot_id, state=liquidity)

        candidates = run_candidate_pipeline_g31(
            conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity,
        )
        stats.candidates = len(candidates)

        signal = evaluate_live_signal_g3(
            conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity,
            candidates=candidates,
        )
        if signal:
            stats.signals = 1
            if not signal.dashboard_only:
                send_live_signal_telegram_g3(conn, signal)

        stats.followups = check_signal_followups_g3(conn)
        maybe_send_daily_report_g3(conn)

        try:
            from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
                maybe_send_calibration_daily_g34,
            )
            maybe_send_calibration_daily_g34(conn)
        except Exception as exc:
            logger.debug("g34 daily digest skipped: %s", exc)

        try:
            from bot.research.market_events.signal_intelligence.telegram_intelligence_g35 import (
                maybe_send_candidate_alerts_g35,
                maybe_send_claude_insight_g35,
                maybe_send_daily_research_g35,
                maybe_send_hourly_market_brief_g35,
                maybe_send_upgrades_downgrades_g35,
            )
            maybe_send_hourly_market_brief_g35(conn)
            maybe_send_candidate_alerts_g35(conn)
            maybe_send_upgrades_downgrades_g35(conn)
            maybe_send_daily_research_g35(conn)
            maybe_send_claude_insight_g35(conn)
        except Exception as exc:
            logger.debug("g35 telegram intelligence skipped: %s", exc)

        try:
            from bot.research.market_events.signal_intelligence.missed_opportunities_g32 import (
                maybe_send_missed_opportunities_g32,
            )
            from bot.research.market_events.signal_intelligence.replay_g32 import maybe_run_replay_g32
            stats.replay_updates = maybe_run_replay_g32(conn)
            maybe_send_missed_opportunities_g32(conn)
        except Exception as exc:
            logger.debug("g32 replay skipped: %s", exc)

        try:
            from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
                maybe_run_validation_g4,
                maybe_send_validation_daily_g4,
            )
            maybe_run_validation_g4(conn)
            maybe_send_validation_daily_g4(conn)
        except Exception as exc:
            logger.debug("g4 validation skipped: %s", exc)

        try:
            from bot.research.market_events.signal_intelligence.research_lake_g51 import (
                maybe_run_research_lake_g51,
            )
            maybe_run_research_lake_g51(conn)
        except Exception as exc:
            logger.debug("g51 research lake skipped: %s", exc)

        try:
            from bot.research.market_events.signal_intelligence.quant_research_g50 import (
                maybe_run_quant_research_nightly_g50,
            )
            maybe_run_quant_research_nightly_g50(conn)
        except Exception as exc:
            logger.debug("g50 quant research skipped: %s", exc)

        from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state
        from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
            write_system_heartbeat,
        )
        set_g3_ops_state(conn, "last_cycle_ts", str(int(time.time())))
        write_system_heartbeat(conn, writer="g3-live")
    except Exception as exc:
        stats.errors = 1
        stats.error_messages.append(str(exc))
        logger.exception("g3 cycle failed: %s", exc)
        from bot.research.market_events.signal_intelligence.health_g3 import update_recorder_health_g3
        update_recorder_health_g3(conn, snapshot_id=None, latency_ms=None, status="error", error=str(exc))
    return stats


def run_g3_live(
    *,
    max_cycles: int | None = None,
    interval_sec: int | None = None,
) -> G3CycleStats:
    """Run G3 background loop until max_cycles or forever."""
    if not G3_ENABLED:
        logger.info("G3 disabled (ME_G3_LIVE_SIGNAL=false)")
        return G3CycleStats()

    interval = interval_sec if interval_sec is not None else G3_RECORDER_INTERVAL_SEC
    total = G3CycleStats()
    cycle = 0

    with market_events_connection() as conn:
        apply_migrations(conn)

    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        with market_events_connection() as conn:
            apply_migrations(conn)
            stats = run_g3_cycle(conn)
            conn.commit()
        total.cycles += stats.cycles
        total.snapshots += stats.snapshots
        total.trends += stats.trends
        total.candidates += stats.candidates
        total.signals += stats.signals
        total.followups += stats.followups
        total.replay_updates += stats.replay_updates
        total.errors += stats.errors
        total.error_messages.extend(stats.error_messages)
        if stats.last_snapshot_id:
            total.last_snapshot_id = stats.last_snapshot_id

        if max_cycles is not None and cycle >= max_cycles:
            break
        time.sleep(interval)

    return total
