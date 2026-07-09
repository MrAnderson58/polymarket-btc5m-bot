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
from bot.research.market_events.universe import select_universe

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
        feed: BinanceFuturesPriceFeed | None = None,
    ) -> None:
        self.universe_mode = universe_mode
        self.paper_only = paper_only
        self.max_cycles = max_cycles
        self.feed = feed or BinanceFuturesPriceFeed()
        self.stats = RunnerStats()
        self._active: dict[int, ActiveEvent] = {}
        self._shutdown = False

    def request_shutdown(self) -> None:
        self._shutdown = True

    def _persist_event(self, conn: Any, shock: ShockCandidate, classification: str) -> int | None:
        try:
            return insert_returning_id(
                conn,
                """
                INSERT INTO market_events (
                  event_ts, detected_ts, venue, symbol, event_type, direction, phase,
                  trigger_window_seconds, return_pct, velocity, acceleration, volume_zscore,
                  market_return_pct, btc_return_pct, relative_return_pct, classification,
                  confidence, detector_version, detector_triggers_json, dedup_key,
                  raw_metrics_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shock.event_ts, shock.detected_ts, "binance_futures", shock.symbol,
                    "SHOCK", shock.direction, EVENT_PHASE_DETECTED,
                    max(t.window_sec for t in shock.triggers),
                    shock.return_pct, shock.velocity, shock.acceleration, shock.volume_zscore,
                    shock.market_return_pct, shock.btc_return_pct, shock.relative_return_pct,
                    classification, shock.confidence, DETECTOR_VERSION,
                    json.dumps([t.detector_id for t in shock.triggers]),
                    shock.dedup_key, json.dumps(shock.raw_metrics), int(time.time()),
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

    def run_once(self, conn: Any, symbols: list[str]) -> None:
        now = int(time.time())
        self.feed.poll_universe(symbols, max_age_sec=PRICE_HISTORY_SEC)
        self.stats.polls += 1

        shocks = scan_universe_for_shocks(self.feed, symbols, now_ts=now)
        for shock in shocks:
            eth_st = self.feed.get_state("ETH")
            eth_ret = eth_st.return_over(60, now) if eth_st else None
            classification = classify_shock(shock, eth_return_pct=eth_ret, median_universe_return_pct=shock.market_return_pct)
            event_id = self._persist_event(conn, shock, classification)
            if event_id is None:
                continue
            self.stats.shocks_detected += 1
            state = self.feed.get_state(shock.symbol)
            if state:
                persist_snapshot(conn, event_id=event_id, snapshot_ts=now, offset_seconds=0, state=state, event_price=state.last_price or 0)
            link_event_context(conn, event_id=event_id, event_ts=shock.event_ts, symbol=shock.symbol)

            if not state:
                continue
            revs = evaluate_all_reversals(shock, state, now)
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
            symbols, version = select_universe(conn, mode=self.universe_mode)
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
) -> RunnerStats:
    return ShockPaperRunner(
        universe_mode=universe, paper_only=paper_only, max_cycles=max_cycles,
    ).run()
