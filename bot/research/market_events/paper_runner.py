"""Phase E.1 paper shock runner — poll, detect, confirm, paper trade."""

from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events import DETECTOR_VERSION, PAPER_ENGINE_VERSION
from bot.research.market_events.config import (
    DEFAULT_HEARTBEAT_SEC,
    EXIT_POLICIES,
    POLL_INTERVAL_SEC,
    PRICE_HISTORY_SEC,
    REVERSAL_CONFIGS,
)
from bot.research.market_events.collector_heartbeat import CollectorMetrics
from bot.research.market_events.db import insert_returning_id, market_events_connection
from bot.research.market_events.detector_diagnostics import (
    REJECTION_DUPLICATE,
    diagnose_universe,
)
from bot.research.market_events.event_context_linker import link_event_context
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.event_types import (
    EVENT_PHASE_DETECTED,
    EVENT_PHASE_PAPER_ENTRY,
    EXIT_IDS,
    REVERSAL_IDS,
)
from bot.research.market_events.market_snapshot import persist_snapshot
from bot.research.market_events.paper_execution import (
    PaperPosition,
    net_return,
    open_paper_position,
    process_exit_tick,
)
from bot.research.market_events.pending_reversal import (
    PHASE_MANAGING,
    create_pending_shock,
    process_pending_shock,
    restore_pending_map,
)
from bot.research.market_events.near_miss_shadow import (
    persist_near_miss_snapshots,
    update_near_miss_from_state,
)
from bot.research.market_events.price_feed import BinanceFuturesPriceFeed
from bot.research.market_events.reversal_confirmation import evaluate_all_reversals
from bot.research.market_events.shock_classifier import classify_shock
from bot.research.market_events.shock_detector import ShockCandidate, scan_universe_for_shocks
from bot.research.market_events.universe import needs_multi_venue_feed, select_universe

logger = logging.getLogger(__name__)


@dataclass
class RunnerStats:
    polls: int = 0
    shocks_detected: int = 0
    shocks_deduped: int = 0
    paper_entries: int = 0
    paper_closed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ActiveEvent:
    event_id: int
    shock: ShockCandidate
    r5_first_confirm: int | None = None
    positions: list[PaperPosition] = field(default_factory=list)


class ShockPaperRunner:
    def __init__(
        self,
        *,
        universe_mode: str = "core",
        paper_only: bool = True,
        max_cycles: int | None = None,
        explicit_symbols: list[str] | None = None,
        feed: BinanceFuturesPriceFeed | None = None,
        heartbeat_sec: int | None = None,
    ) -> None:
        self.universe_mode = universe_mode
        self.paper_only = paper_only
        self.max_cycles = max_cycles
        self.explicit_symbols = explicit_symbols
        self.feed = feed or BinanceFuturesPriceFeed()
        self.heartbeat_sec = heartbeat_sec if heartbeat_sec is not None else DEFAULT_HEARTBEAT_SEC
        self.stats = RunnerStats()
        self.metrics = CollectorMetrics()
        self._near_miss: dict = {}
        self._active: dict[int, ActiveEvent] = {}
        self._pending: dict[int, Any] = {}
        self._shutdown = False
        self._instrument_map: dict[str, dict[str, Any]] = {}
        self._last_f72_tick = 0

    def request_shutdown(self) -> None:
        self._shutdown = True

    def _persist_event(
        self,
        conn: Any,
        shock: ShockCandidate,
        classification: str,
        *,
        venue: str = "binance_futures",
        instrument_id: int | None = None,
        asset_class: str | None = None,
        session_regime: str | None = None,
        reference_return_pct: float | None = None,
        basis_bps: float | None = None,
        cross_classification: str | None = None,
    ) -> int | None:
        try:
            return insert_returning_id(
                conn,
                """
                INSERT INTO market_events (
                  event_ts, detected_ts, venue, symbol, event_type, direction, phase,
                  trigger_window_seconds, return_pct, velocity, acceleration, volume_zscore,
                  market_return_pct, btc_return_pct, relative_return_pct, classification,
                  confidence, detector_version, detector_triggers_json, dedup_key,
                  raw_metrics_json, created_at,
                  instrument_id, asset_class, session_regime, reference_return_pct,
                  basis_bps, cross_classification
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shock.event_ts, shock.detected_ts, venue, shock.symbol,
                    "SHOCK", shock.direction, EVENT_PHASE_DETECTED,
                    max(t.window_sec for t in shock.triggers),
                    shock.return_pct, shock.velocity, shock.acceleration, shock.volume_zscore,
                    shock.market_return_pct, shock.btc_return_pct, shock.relative_return_pct,
                    classification, shock.confidence, DETECTOR_VERSION,
                    json.dumps([t.detector_id for t in shock.triggers]),
                    shock.dedup_key, json.dumps(shock.raw_metrics), int(time.time()),
                    instrument_id, asset_class, session_regime, reference_return_pct,
                    basis_bps, cross_classification,
                ),
            )
        except Exception:
            self.stats.shocks_deduped += 1
            self.metrics.detector_diag.record("SHOCK_A", REJECTION_DUPLICATE, symbol=shock.symbol)
            return None

    def _open_paper_runs(
        self,
        conn: Any,
        event_id: int,
        shock: ShockCandidate,
        entry_ts: int,
        entry_price: float,
        reversal: str,
    ) -> None:
        created = 0
        for exit_id in EXIT_IDS:
            strategy_name = f"REVERSAL_{reversal}_{exit_id}"
            pos = open_paper_position(
                event_id=event_id,
                symbol=shock.symbol,
                direction=shock.direction,
                reversal_variant=reversal,
                exit_variant=exit_id,
                entry_ts=entry_ts,
                entry_price=entry_price,
            )
            cfg = EXIT_POLICIES[exit_id]
            row_id = insert_returning_id(
                conn,
                """
                INSERT OR IGNORE INTO paper_strategy_runs (
                  event_id, strategy_name, strategy_version, reversal_variant, exit_variant,
                  eligibility, rejection_reason, signal_ts, entry_ts, entry_price,
                  initial_stop, breakeven_trigger, trailing_mode, take_profit_mode,
                  fee_bps, slippage_bps, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, strategy_name, PAPER_ENGINE_VERSION, reversal, exit_id,
                    entry_ts, entry_ts, entry_price, pos.initial_stop,
                    cfg.get("be_trigger_pct"), exit_id, str(cfg.get("tp_pct")),
                    10.0, 10.0, int(time.time()),
                ),
            )
            if row_id == 0:
                continue
            created += 1
            if event_id in self._active:
                self._active[event_id].positions.append(pos)
            elif event_id in self._pending:
                self._pending[event_id].positions.append(pos)
            self.stats.paper_entries += 1

        if created == 0:
            return

        try:
            from bot.research.market_events.market_event_alerts import alert_reversal_confirmed
            pending = conn.execute(
                "SELECT * FROM market_events_pending_shocks WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            path = json.loads(pending["path_from_extreme_json"] or "null") if pending else None
            alert_reversal_confirmed(
                conn,
                event_id=event_id,
                reversal_variant=reversal,
                confirm_latency_sec=int(pending["confirm_latency_sec"]) if pending and pending["confirm_latency_sec"] else None,
                extreme_price=float(pending["shock_extreme_price"]) if pending and pending["shock_extreme_price"] else None,
                path_json=path if isinstance(path, dict) else None,
                paper_runs=created,
            )
        except Exception as exc:
            logger.debug("reversal alert skipped: %s", exc)

        try:
            from bot.research.market_events.market_event_alerts import alert_paper_position_update
            alert_paper_position_update(
                conn,
                event_id=event_id,
                update_type="PAPER_ENTRY",
                symbol=shock.symbol,
                reversal_variant=reversal,
                exit_variant="ALL",
                detail=f"entry_price={entry_price}",
            )
        except Exception:
            pass

    def _classify_shock(self, shock: ShockCandidate, symbols: list[str], now: int) -> tuple[str, str | None, float | None, float | None, str | None, str | None, int | None]:
        eth_st = self.feed.get_state("ETH")
        eth_ret = eth_st.return_over(60, now) if eth_st else None
        classification = classify_shock(
            shock, eth_return_pct=eth_ret, median_universe_return_pct=shock.market_return_pct,
        )
        if not self._instrument_map:
            return classification, None, None, None, None, None, None, "binance_futures"

        inst = self._instrument_map.get(shock.symbol.upper())
        asset_class = inst["asset_class"] if inst else None
        instrument_id = int(inst["id"]) if inst else None
        venue = inst["venue"] if inst else "binance_futures"
        session_regime = None
        if inst:
            from bot.research.market_events.session_regime import classify_session_regime
            session_regime = classify_session_regime(
                now, asset_class=inst["asset_class"], trading_hours_mode=inst["trading_hours_mode"],
            )

        basis_bps = getattr(self.feed, "get_basis_bps", lambda _s: None)(shock.symbol)
        ref_ret = None
        if hasattr(self.feed, "get_reference_return_pct"):
            ref_ret = self.feed.get_reference_return_pct(shock.symbol, 60, now)

        peer_returns: dict[str, float] = {}
        for sym in symbols:
            st = self.feed.get_state(sym)
            if st:
                r = st.return_over(60, now)
                if r is not None:
                    peer_returns[sym] = r

        btc_st = self.feed.get_state("BTC")
        btc_ret = btc_st.return_over(60, now) if btc_st else None
        from bot.research.market_events.cross_asset_classifier import classify_cross_asset
        cross = classify_cross_asset(
            shock,
            asset_class=asset_class or "CRYPTO",
            canonical_asset=shock.symbol,
            basis_bps=basis_bps,
            reference_return_pct=ref_ret,
            peer_returns=peer_returns,
            btc_return_pct=btc_ret,
            median_crypto_return=shock.market_return_pct,
        )
        return classification, cross, asset_class, session_regime, ref_ret, basis_bps, instrument_id, venue

    def _shock_allowed(self, symbol: str, now: int) -> tuple[bool, str | None]:
        inst = self._instrument_map.get(symbol.upper())
        if not inst or inst.get("asset_class") == "CRYPTO":
            return True, None
        if not inst.get("paper_enabled"):
            return False, "not_paper_enabled"
        from bot.research.market_events.activation_rules import is_shock_eligible
        from bot.research.market_events.quote_quality import evaluate_bybit_quote
        from bot.research.market_events.session_regime import classify_session_regime

        session = classify_session_regime(
            now, asset_class=inst["asset_class"], trading_hours_mode=inst["trading_hours_mode"],
        )
        ticker = getattr(self.feed, "get_last_ticker", lambda _s: None)(symbol)
        qq = evaluate_bybit_quote(ticker, poll_ts=now)
        return is_shock_eligible(
            asset_class=inst["asset_class"],
            session_regime=session,
            quote_ok=qq.ok,
            rejection_reason=qq.rejection_reason,
        )

    def _process_pending_shocks(self, conn: Any, symbols: list[str], now: int) -> None:
        expired: list[int] = []
        for eid, pending in list(self._pending.items()):
            state = self.feed.get_state(pending.symbol)
            if not state:
                continue
            result = process_pending_shock(
                conn, pending, state, now,
                open_paper_fn=lambda evt, shock, ets, px, rev: self._open_paper_runs(
                    conn, evt, shock, ets, px, rev,
                ),
            )
            if result in ("expired", "done"):
                expired.append(eid)
            elif pending.phase == PHASE_MANAGING and eid not in self._active:
                self._active[eid] = ActiveEvent(
                    event_id=eid, shock=pending.shock, positions=pending.positions,
                )
        for eid in expired:
            del self._pending[eid]

    def _restore_state(self, conn: Any) -> None:
        self._pending = restore_pending_map(conn)
        rows = conn.execute(
            """
            SELECT DISTINCT event_id, reversal_variant FROM paper_strategy_runs
            WHERE exit_ts IS NULL
            """,
        ).fetchall()
        for r in rows:
            eid = int(r["event_id"])
            evt = conn.execute("SELECT * FROM market_events WHERE id = ?", (eid,)).fetchone()
            if not evt:
                continue
            from bot.research.market_events.shock_detector import ShockCandidate, ShockTrigger
            triggers = [
                ShockTrigger(t, 60, float(evt["return_pct"] or 0))
                for t in json.loads(evt["detector_triggers_json"] or "[]")
            ]
            shock = ShockCandidate(
                symbol=evt["symbol"], direction=evt["direction"],
                event_ts=int(evt["event_ts"]), detected_ts=int(evt["detected_ts"]),
                triggers=triggers, return_pct=float(evt["return_pct"] or 0),
            )
            if eid not in self._active:
                self._active[eid] = ActiveEvent(event_id=eid, shock=shock)
            open_runs = conn.execute(
                """
                SELECT reversal_variant, exit_variant, entry_ts, entry_price
                FROM paper_strategy_runs WHERE event_id = ? AND exit_ts IS NULL
                """,
                (eid,),
            ).fetchall()
            for run in open_runs:
                pos = open_paper_position(
                    event_id=eid, symbol=shock.symbol, direction=shock.direction,
                    reversal_variant=run["reversal_variant"], exit_variant=run["exit_variant"],
                    entry_ts=int(run["entry_ts"]), entry_price=float(run["entry_price"]),
                )
                self._active[eid].positions.append(pos)

    def _count_open_paper_runs(self, conn: Any) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_strategy_runs WHERE exit_ts IS NULL",
        ).fetchone()
        return int(row["n"] if row else 0)

    def _maybe_heartbeat(self, conn: Any, symbols: list[str]) -> None:
        now = int(time.time())
        if not self.metrics.should_heartbeat(now, self.heartbeat_sec):
            return
        open_runs = self._count_open_paper_runs(conn)
        text = self.metrics.render_heartbeat(
            universe=self.universe_mode,
            events_detected=self.stats.shocks_detected,
            pending_reversals=len(self._pending),
            paper_runs_open=open_runs,
        )
        self.metrics.emit_heartbeat(text)
        try:
            from bot.research.market_events.alert_engine.scheduler import scheduler_tick
            scheduler_tick(conn)
        except Exception as exc:
            logger.debug("scheduler tick skipped: %s", exc)
        if self._near_miss:
            persist_near_miss_snapshots(
                conn, self._near_miss,
                period_start=self.metrics.startup_ts, period_end=now,
            )

    def run_once(self, conn: Any, symbols: list[str]) -> None:
        cycle_start = time.perf_counter()
        now = int(time.time())
        poll_out = self.feed.poll_universe(symbols, max_age_sec=PRICE_HISTORY_SEC)
        fetch_ok = len(poll_out)
        fetch_failed = len(symbols) - fetch_ok
        self.stats.polls += 1

        btc = self.feed.get_state("BTC")
        for sym in symbols:
            state = self.feed.get_state(sym)
            if not state:
                continue
            session = None
            inst = self._instrument_map.get(sym.upper())
            if inst:
                from bot.research.market_events.session_regime import classify_session_regime
                session = classify_session_regime(
                    now, asset_class=inst["asset_class"],
                    trading_hours_mode=inst["trading_hours_mode"],
                )
            update_near_miss_from_state(
                self._near_miss, state, now_ts=now,
                btc_state=btc, session_regime=session,
            )

        diag = diagnose_universe(
            self.feed, symbols, now_ts=now,
            shock_allowed_fn=self._shock_allowed if self._instrument_map else None,
        )
        self.metrics.detector_diag = diag

        self._process_pending_shocks(conn, symbols, now)
        shocks = scan_universe_for_shocks(self.feed, symbols, now_ts=now)
        for shock in shocks:
            allowed, skip_reason = self._shock_allowed(shock.symbol, now)
            if not allowed:
                logger.debug("shock skipped %s: %s", shock.symbol, skip_reason)
                continue
            cls_result = self._classify_shock(shock, symbols, now)
            if self.universe_mode != "core" or self._instrument_map:
                classification, cross, asset_class, session_regime, ref_ret, basis_bps, instrument_id, venue = cls_result
            else:
                classification = cls_result[0]
                cross = asset_class = session_regime = ref_ret = basis_bps = instrument_id = None
                venue = "binance_futures"

            eth_st = self.feed.get_state("ETH")
            if eth_st:
                window = shock.triggers[0].window_sec if shock.triggers else 60
                eth_ret = eth_st.return_over(window, now)
                if eth_ret is not None:
                    shock.raw_metrics["eth_return_pct"] = eth_ret

            event_id = self._persist_event(
                conn, shock, classification,
                venue=venue,
                instrument_id=instrument_id,
                asset_class=asset_class,
                session_regime=session_regime,
                reference_return_pct=ref_ret,
                basis_bps=basis_bps,
                cross_classification=cross,
            )
            if event_id is None:
                continue
            self.stats.shocks_detected += 1
            try:
                from bot.research.market_events.signal_intelligence.signal_trace_f51 import record_shock_received
                record_shock_received(conn, event_id=event_id)
            except Exception as exc:
                logger.debug("f51 raw trace skipped: %s", exc)
            state = self.feed.get_state(shock.symbol)
            if state:
                ref_price = getattr(self.feed, "get_reference_price", lambda _s: None)(shock.symbol)
                snap_basis = basis_bps
                tracking = None
                if ref_price and state.last_price and ref_price > 0:
                    tracking = (state.last_price / ref_price - 1.0) * 10000.0
                persist_snapshot(
                    conn, event_id=event_id, snapshot_ts=now, offset_seconds=0, state=state,
                    event_price=state.last_price or 0,
                    reference_price=ref_price,
                    basis_bps=snap_basis,
                    tracking_error_bps=tracking,
                )
            link_event_context(conn, event_id=event_id, event_ts=shock.event_ts, symbol=shock.symbol)
            try:
                from bot.research.market_events.ai_analyst.job_queue import enqueue_analysis_job
                enqueue_analysis_job(conn, event_id=event_id)
            except Exception as exc:
                logger.debug("analysis job enqueue skipped: %s", exc)
            try:
                from bot.research.market_events.market_event_alerts import alert_shock_detected
                alert_shock_detected(conn, event_id)
            except Exception as exc:
                logger.debug("shock alert skipped: %s", exc)
            try:
                from bot.research.market_events.alert_engine.scheduler import on_shock_detected
                on_shock_detected(conn, event_id=event_id)
            except Exception as exc:
                logger.debug("e5 shock hook skipped: %s", exc)
            try:
                from bot.research.market_events.signal_intelligence.hooks import on_shock_f0
                on_shock_f0(conn, event_id=event_id)
            except Exception as exc:
                logger.debug("f0 shock hook skipped: %s", exc)

            if state:
                create_pending_shock(
                    conn,
                    event_id=event_id,
                    symbol=shock.symbol,
                    direction=shock.direction,
                    detected_ts=shock.detected_ts,
                    shock_return_pct=shock.return_pct,
                    extreme_price=state.last_price,
                )
                from bot.research.market_events.pending_reversal import row_to_pending_state
                pending_row = conn.execute(
                    "SELECT * FROM market_events_pending_shocks WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if pending_row:
                    ps = row_to_pending_state(pending_row)
                    ps.shock = shock
                    self._pending[event_id] = ps
                    process_pending_shock(
                        conn, ps, state, now,
                        open_paper_fn=lambda evt, sh, ets, px, rev: self._open_paper_runs(
                            conn, evt, sh, ets, px, rev,
                        ),
                    )
                    if ps.phase == PHASE_MANAGING and event_id not in self._active:
                        self._active[event_id] = ActiveEvent(
                            event_id=event_id, shock=shock, positions=ps.positions,
                        )
                revs = evaluate_all_reversals(shock, state, now)
                from bot.research.market_events.lifecycle_decisions import persist_reversal_decisions
                persist_reversal_decisions(conn, event_id=event_id, decision_ts=now, results=revs)

        self._process_open_positions(conn, now)

        try:
            from bot.research.market_events.signal_intelligence.config import (
                F72_CHECK_INTERVAL_SEC,
                F72_ENABLED,
            )
            if F72_ENABLED and now - self._last_f72_tick >= F72_CHECK_INTERVAL_SEC:
                from bot.research.market_events.signal_intelligence.signal_outcome_f72 import (
                    tick_active_signals_f72,
                )
                from bot.research.market_events.signal_intelligence.yesterday_report_f72 import (
                    maybe_send_morning_digest_f72,
                )
                tick_active_signals_f72(conn, feed=self.feed, now=now)
                maybe_send_morning_digest_f72(conn, now=now)
                try:
                    from bot.research.market_events.signal_intelligence.config import F73_ENABLED
                    if F73_ENABLED:
                        from bot.research.market_events.signal_intelligence.reports_f73 import (
                            maybe_send_quiet_market_f73,
                        )
                        maybe_send_quiet_market_f73(conn, now=now)
                except Exception as exc:
                    logger.debug("f73 quiet market skipped: %s", exc)
                self._last_f72_tick = now
        except Exception as exc:
            logger.debug("f72 signal outcome tick skipped: %s", exc)

        latency_ms = (time.perf_counter() - cycle_start) * 1000.0
        self.metrics.record_cycle(latency_ms, fetch_ok=fetch_ok, fetch_failed=fetch_failed)

    def _process_open_positions(self, conn: Any, now: int) -> None:
        for eid, active in list(self._active.items()):
            state = self.feed.get_state(active.shock.symbol)
            if not state or state.last_price is None:
                continue
            price = state.last_price
            all_closed = True
            for pos in active.positions:
                if pos.closed:
                    continue
                all_closed = False
                tick = process_exit_tick(pos, ts=now, price=price)
                if not pos.closed and pos.be_active and pos.stop_history:
                    last = pos.stop_history[-1]
                    if last.get("reason") == "BE_ACTIVATE":
                        try:
                            from bot.research.market_events.market_event_alerts import (
                                alert_paper_position_update,
                            )
                            alert_paper_position_update(
                                conn,
                                event_id=eid,
                                update_type="MOVE_TO_BE",
                                symbol=active.shock.symbol,
                                reversal_variant=pos.reversal_variant,
                                exit_variant=pos.exit_variant,
                            )
                        except Exception:
                            pass
                if tick.closed and pos.exit_ts:
                    import json as _json
                    conn.execute(
                        """
                        UPDATE paper_strategy_runs SET
                          exit_ts=?, exit_price=?, exit_reason=?, gross_return=?,
                          net_return=?, mfe=?, mae=?, be_exit=?, duration_seconds=?,
                          stop_history_json=?, be_helped=?
                        WHERE event_id=? AND reversal_variant=? AND exit_variant=?
                        """,
                        (
                            pos.exit_ts, pos.exit_price, pos.exit_reason, pos.gross_return,
                            net_return(pos.gross_return or 0.0),
                            pos.mfe, pos.mae, 1 if pos.be_exit else 0,
                            pos.exit_ts - pos.entry_ts,
                            _json.dumps(pos.stop_history), pos.be_helped,
                            eid, pos.reversal_variant, pos.exit_variant,
                        ),
                    )
                    self.stats.paper_closed += 1
                    try:
                        from bot.research.market_events.market_event_alerts import (
                            alert_paper_position_update,
                        )
                        reason = pos.exit_reason or "CLOSED"
                        update_type = reason if reason in ("TP", "STOP", "BE_STOP") else "CLOSED"
                        if update_type == "BE_STOP":
                            update_type = "STOP"
                        alert_paper_position_update(
                            conn,
                            event_id=eid,
                            update_type=update_type,
                            symbol=active.shock.symbol,
                            reversal_variant=pos.reversal_variant,
                            exit_variant=pos.exit_variant,
                            detail=f"gross_return={pos.gross_return:.3f}%" if pos.gross_return else "",
                        )
                    except Exception:
                        pass
                    try:
                        from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
                            record_paper_close_f6,
                        )
                        record_paper_close_f6(
                            conn,
                            event_id=eid,
                            net_return=net_return(pos.gross_return or 0.0),
                            exit_reason=pos.exit_reason,
                            duration_seconds=pos.exit_ts - pos.entry_ts if pos.exit_ts else None,
                            mfe=pos.mfe,
                            mae=pos.mae,
                            gross_return=pos.gross_return,
                            reversal_variant=pos.reversal_variant,
                            exit_variant=pos.exit_variant,
                        )
                    except Exception as exc:
                        logger.debug("f6 trader performance skipped: %s", exc)
                    try:
                        from bot.research.market_events.signal_intelligence.reversal_learning_g1 import (
                            record_reversal_learning_g1,
                        )
                        evt = conn.execute(
                            "SELECT direction FROM market_events WHERE id = ?",
                            (eid,),
                        ).fetchone()
                        record_reversal_learning_g1(
                            conn,
                            event_id=eid,
                            symbol=active.shock.symbol,
                            entry_ts=pos.entry_ts,
                            shock_direction=str(evt["direction"] if evt else "DOWN"),
                            net_return=net_return(pos.gross_return or 0.0),
                        )
                    except Exception as exc:
                        logger.debug("g1 reversal learning skipped: %s", exc)
                    try:
                        from bot.research.market_events.signal_intelligence.learning_g2 import (
                            record_paper_learning_g2,
                        )
                        record_paper_learning_g2(
                            conn,
                            event_id=eid,
                            symbol=active.shock.symbol,
                            entry_ts=pos.entry_ts,
                            entry_price=float(pos.entry_price or 0),
                            net_return=net_return(pos.gross_return or 0.0),
                            exit_reason=pos.exit_reason,
                            duration_seconds=pos.exit_ts - pos.entry_ts if pos.exit_ts else None,
                        )
                    except Exception as exc:
                        logger.debug("g2 paper learning skipped: %s", exc)
                    try:
                        from bot.research.market_events.alert_engine.scheduler import on_event_resolved
                        on_event_resolved(conn, event_id=eid)
                    except Exception:
                        pass
            if all_closed:
                del self._active[eid]

    def run(self) -> RunnerStats:
        logging.basicConfig(level=logging.INFO, format="[shock-paper] %(message)s")
        logger.info("startup paper_only=%s universe=%s", self.paper_only, self.universe_mode)

        try:
            from bot.research.market_events.telegram_ops.startup_validation import (
                validate_telegram_config_at_startup,
            )
            validate_telegram_config_at_startup()
        except Exception as exc:
            logger.debug("telegram startup validation skipped: %s", exc)

        def _handle_sig(signum, frame):
            logger.info("shutdown signal %s", signum)
            self.request_shutdown()

        signal.signal(signal.SIGINT, _handle_sig)
        signal.signal(signal.SIGTERM, _handle_sig)

        with market_events_connection() as conn:
            from bot.research.market_events.startup_lock import market_events_startup_lock

            with market_events_startup_lock():
                apply_migrations(conn)
                symbols, version = select_universe(
                    conn, mode=self.universe_mode, explicit_symbols=self.explicit_symbols,
                )
                if needs_multi_venue_feed(self.universe_mode, self.explicit_symbols):
                    from bot.research.market_events.instrument_master import (
                        load_active_instruments,
                        load_paper_instruments,
                        resolve_instruments_by_symbols,
                    )
                    from bot.research.market_events.multi_venue_feed import MultiVenuePriceFeed
                    if self.explicit_symbols:
                        instruments = resolve_instruments_by_symbols(conn, self.explicit_symbols)
                    elif self.universe_mode == "tradfi-liquid":
                        instruments = [dict(r) for r in load_paper_instruments(conn, tradfi_only=True)]
                    elif self.universe_mode == "multi-paper":
                        instruments = [dict(r) for r in load_paper_instruments(conn)]
                    elif self.universe_mode == "multi":
                        instruments = [dict(r) for r in load_active_instruments(conn)]
                    else:
                        instruments = [dict(r) for r in load_paper_instruments(conn)]
                    self.feed = MultiVenuePriceFeed(instruments)
                    self._instrument_map = {r["canonical_asset"]: r for r in instruments}
                    logger.info("multi-venue feed instruments=%s", len(instruments))
                logger.info("universe %s symbols=%s", version, ",".join(symbols))
                self._restore_state(conn)

        try:
            from bot.research.market_events.ai_analyst.config import AI_EMBEDDED_IN_PAPER_RUN
            if AI_EMBEDDED_IN_PAPER_RUN:
                from bot.research.market_events.ai_analyst.job_queue import start_background_worker
                start_background_worker(market_events_connection)
        except Exception as exc:
            logger.debug("AI background worker not started: %s", exc)

        cycles = 0
        while not self._shutdown:
            try:
                with market_events_connection() as conn:
                    self.run_once(conn, symbols)
                    try:
                        from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
                            write_system_heartbeat,
                        )
                        write_system_heartbeat(conn, writer="shock-paper")
                    except Exception as exc:
                        logger.debug("shock-paper heartbeat write skipped: %s", exc)
                    self._maybe_heartbeat(conn, symbols)
            except Exception as exc:
                self.stats.errors.append(str(exc))
                logger.error("cycle error: %s", exc)
            cycles += 1
            if self.max_cycles and cycles >= self.max_cycles:
                break
            time.sleep(POLL_INTERVAL_SEC)

        logger.info(
            "complete polls=%s shocks=%s entries=%s closed=%s deduped=%s",
            self.stats.polls, self.stats.shocks_detected,
            self.stats.paper_entries, self.stats.paper_closed, self.stats.shocks_deduped,
        )
        return self.stats


def run_shock_paper(
    *,
    universe: str = "core",
    paper_only: bool = True,
    max_cycles: int | None = None,
    explicit_symbols: list[str] | None = None,
    heartbeat_sec: int | None = None,
) -> RunnerStats:
    return ShockPaperRunner(
        universe_mode=universe,
        paper_only=paper_only,
        max_cycles=max_cycles,
        explicit_symbols=explicit_symbols,
        heartbeat_sec=heartbeat_sec,
    ).run()
