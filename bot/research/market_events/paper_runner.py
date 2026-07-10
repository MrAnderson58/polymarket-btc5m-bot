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
    EXIT_POLICIES,
    POLL_INTERVAL_SEC,
    PRICE_HISTORY_SEC,
    REVERSAL_CONFIGS,
)
from bot.research.market_events.db import insert_returning_id, market_events_connection
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
    ) -> None:
        self.universe_mode = universe_mode
        self.paper_only = paper_only
        self.max_cycles = max_cycles
        self.explicit_symbols = explicit_symbols
        self.feed = feed or BinanceFuturesPriceFeed()
        self.stats = RunnerStats()
        self._active: dict[int, ActiveEvent] = {}
        self._shutdown = False
        self._instrument_map: dict[str, dict[str, Any]] = {}

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
            insert_returning_id(
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
            if event_id in self._active:
                self._active[event_id].positions.append(pos)
            self.stats.paper_entries += 1

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

    def run_once(self, conn: Any, symbols: list[str]) -> None:
        now = int(time.time())
        self.feed.poll_universe(symbols, max_age_sec=PRICE_HISTORY_SEC)
        self.stats.polls += 1

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

            if not state:
                continue
            revs = evaluate_all_reversals(shock, state, now)
            from bot.research.market_events.lifecycle_decisions import persist_reversal_decisions
            persist_reversal_decisions(conn, event_id=event_id, decision_ts=now, results=revs)
            for rev in revs:
                if not rev.confirmed or rev.confirm_ts is None or rev.confirm_price is None:
                    continue
                self._open_paper_runs(conn, event_id, shock, rev.confirm_ts, rev.confirm_price, rev.variant)
            self._active[event_id] = ActiveEvent(event_id=event_id, shock=shock)

        self._process_open_positions(conn, now)

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
                if tick.closed and pos.exit_ts:
                    conn.execute(
                        """
                        UPDATE paper_strategy_runs SET
                          exit_ts=?, exit_price=?, exit_reason=?, gross_return=?,
                          net_return=?, mfe=?, mae=?, be_exit=?, duration_seconds=?
                        WHERE event_id=? AND reversal_variant=? AND exit_variant=?
                        """,
                        (
                            pos.exit_ts, pos.exit_price, pos.exit_reason, pos.gross_return,
                            net_return(pos.gross_return or 0.0),
                            pos.mfe, pos.mae, 1 if pos.be_exit else 0,
                            pos.exit_ts - pos.entry_ts,
                            eid, pos.reversal_variant, pos.exit_variant,
                        ),
                    )
                    self.stats.paper_closed += 1
            if all_closed:
                del self._active[eid]

    def run(self) -> RunnerStats:
        logging.basicConfig(level=logging.INFO, format="[shock-paper] %(message)s")
        logger.info("startup paper_only=%s universe=%s", self.paper_only, self.universe_mode)

        def _handle_sig(signum, frame):
            logger.info("shutdown signal %s", signum)
            self.request_shutdown()

        signal.signal(signal.SIGINT, _handle_sig)
        signal.signal(signal.SIGTERM, _handle_sig)

        with market_events_connection() as conn:
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

            cycles = 0
            while not self._shutdown:
                try:
                    self.run_once(conn, symbols)
                    conn.commit()
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
) -> RunnerStats:
    return ShockPaperRunner(
        universe_mode=universe,
        paper_only=paper_only,
        max_cycles=max_cycles,
        explicit_symbols=explicit_symbols,
    ).run()
