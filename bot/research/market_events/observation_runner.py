"""Observation mode — poll WATCH/PAPER_ACTIVE instruments without paper trades."""

from __future__ import annotations

import json
import logging
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.basis_monitor import observe_basis
from bot.research.market_events.config import POLL_INTERVAL_SEC
from bot.research.market_events.db import insert_returning_id, market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.instrument_master import load_observe_instruments
from bot.research.market_events.multi_venue_feed import MultiVenuePriceFeed
from bot.research.market_events.reference_provider import resolve_bybit_reference
from bot.research.market_events.session_regime import classify_session_regime
from bot.research.market_events.venue_bybit import BybitMarketClient

logger = logging.getLogger(__name__)


@dataclass
class ObserveStats:
    polls: int = 0
    observations: int = 0
    skipped_inactive: int = 0
    errors: list[str] = field(default_factory=list)


class ObservationRunner:
    def __init__(self, *, max_cycles: int | None = None) -> None:
        self.max_cycles = max_cycles
        self.stats = ObserveStats()
        self._shutdown = False

    def request_shutdown(self) -> None:
        self._shutdown = True

    def _persist_observation(
        self,
        conn: Any,
        *,
        instrument: dict[str, Any],
        obs_ts: int,
        ticker,
        session_regime: str,
    ) -> None:
        ref = resolve_bybit_reference(index_price=ticker.index_price, mark_price=ticker.mark_price)
        basis_obs = observe_basis(
            trade_price=ticker.last_price,
            reference_price=ref.price,
            bid=ticker.bid,
            ask=ticker.ask,
        )
        quote_age = max(0.0, float(obs_ts - ticker.ts))
        insert_returning_id(
            conn,
            """
            INSERT INTO market_events_price_observations (
              instrument_id, obs_ts, trade_price, reference_price, basis_bps,
              spread_bps, lag_seconds, venue, session_regime, turnover_24h,
              volume_24h, quote_age_sec, reference_provider, same_venue_reference, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument["id"], obs_ts, ticker.last_price, ref.price, basis_obs.basis_bps,
                basis_obs.spread_bps, basis_obs.lag_seconds, instrument["venue"],
                session_regime, ticker.turnover_24h, ticker.volume_24h, quote_age,
                ref.provider, 1 if ref.same_venue_as_trade else 0,
                json.dumps({
                    "venue_symbol": instrument["venue_symbol"],
                    "activation_tier": instrument.get("activation_tier"),
                    "paper_enabled": instrument.get("paper_enabled"),
                    "reference_provenance": ref.provenance_note,
                }),
            ),
        )
        self.stats.observations += 1

    def run_once(self, conn: Any, instruments: list[dict[str, Any]], bybit: BybitMarketClient) -> None:
        now = int(time.time())
        self.stats.polls += 1
        for inst in instruments:
            if not inst.get("observe_enabled"):
                self.stats.skipped_inactive += 1
                continue
            if inst["venue"] != "bybit_linear":
                continue
            ticker = bybit.fetch_ticker(inst["venue_symbol"])
            if not ticker:
                continue
            session = classify_session_regime(
                now, asset_class=inst["asset_class"], trading_hours_mode=inst["trading_hours_mode"],
            )
            self._persist_observation(conn, instrument=inst, obs_ts=now, ticker=ticker, session_regime=session)

    def run(self) -> ObserveStats:
        logging.basicConfig(level=logging.INFO, format="[observe] %(message)s")
        signal.signal(signal.SIGINT, lambda *_: self.request_shutdown())
        signal.signal(signal.SIGTERM, lambda *_: self.request_shutdown())

        with market_events_connection() as conn:
            apply_migrations(conn)
            instruments = [dict(r) for r in load_observe_instruments(conn)]
            logger.info("observe instruments=%s (WATCH + PAPER_ACTIVE)", len(instruments))
            bybit = BybitMarketClient()
            cycles = 0
            while not self._shutdown:
                try:
                    self.run_once(conn, instruments, bybit)
                    conn.commit()
                except Exception as exc:
                    self.stats.errors.append(str(exc))
                    logger.error("observe cycle error: %s", exc)
                cycles += 1
                if self.max_cycles and cycles >= self.max_cycles:
                    break
                time.sleep(POLL_INTERVAL_SEC)

        logger.info("observe complete polls=%s observations=%s", self.stats.polls, self.stats.observations)
        return self.stats


def run_observe(*, max_cycles: int | None = None) -> ObserveStats:
    return ObservationRunner(max_cycles=max_cycles).run()
