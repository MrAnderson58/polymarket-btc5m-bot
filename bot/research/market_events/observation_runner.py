"""Observation mode — poll WATCH/PAPER_ACTIVE TradFi without paper trades."""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.basis_monitor import observe_basis
from bot.research.market_events.config import POLL_INTERVAL_SEC
from bot.research.market_events.db import insert_returning_id, market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.observe_scope import select_observe_universe
from bot.research.market_events.quote_quality import evaluate_bybit_quote
from bot.research.market_events.reference_provider import resolve_bybit_reference
from bot.research.market_events.session_regime import classify_session_regime
from bot.research.market_events.venue_bybit import BybitMarketClient

logger = logging.getLogger(__name__)


@dataclass
class ObserveStats:
    polls: int = 0
    observations: int = 0
    fetch_failed: int = 0
    stale_recorded: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class InstrumentObserveResult:
    symbol: str
    venue: str
    status: str
    last_price: float | None = None
    index_price: float | None = None
    spread_bps: float | None = None
    basis_bps: float | None = None
    session_regime: str | None = None
    quote_age_sec: float | None = None
    error: str | None = None
    inserted: bool = False


class ObservationRunner:
    def __init__(
        self,
        *,
        universe_mode: str = "tradfi-observe",
        explicit_symbols: list[str] | None = None,
        max_cycles: int | None = None,
    ) -> None:
        self.universe_mode = universe_mode
        self.explicit_symbols = explicit_symbols
        self.max_cycles = max_cycles
        self.stats = ObserveStats()
        self._shutdown = False

    def request_shutdown(self) -> None:
        self._shutdown = True

    def _progress(self, msg: str) -> None:
        print(f"[observe] {msg}", flush=True)
        logger.info(msg)

    def _persist_observation(
        self,
        conn: Any,
        *,
        instrument: dict[str, Any],
        obs_ts: int,
        ticker,
        session_regime: str,
        observe_status: str,
        quote_quality_ok: bool,
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
                    "observe_status": observe_status,
                    "quote_quality_ok": quote_quality_ok,
                    "reference_provenance": ref.provenance_note,
                }),
            ),
        )
        self.stats.observations += 1
        if observe_status == "stale":
            self.stats.stale_recorded += 1

    def _persist_fetch_failure(
        self,
        conn: Any,
        *,
        instrument: dict[str, Any],
        obs_ts: int,
        session_regime: str,
        error: str,
    ) -> None:
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
                instrument["id"], obs_ts, None, None, None,
                None, None, instrument["venue"], session_regime, None,
                None, None, None, 1,
                json.dumps({
                    "venue_symbol": instrument["venue_symbol"],
                    "observe_status": "fetch_failed",
                    "error": error,
                }),
            ),
        )
        self.stats.fetch_failed += 1

    def _observe_one(
        self,
        conn: Any,
        *,
        cycle: int,
        idx: int,
        total: int,
        inst: dict[str, Any],
        bybit: BybitMarketClient,
        now: int,
    ) -> InstrumentObserveResult:
        sym = inst["venue_symbol"]
        canonical = inst["canonical_asset"]
        venue = inst["venue"]
        result = InstrumentObserveResult(symbol=sym, venue=venue, status="pending")

        self._progress(f"cycle={cycle} {idx}/{total} {canonical} venue={venue} fetch_start")

        if venue != "bybit_linear":
            result.status = "skipped_non_bybit"
            self.stats.skipped += 1
            self._progress(
                f"cycle={cycle} {idx}/{total} {canonical} skipped (venue={venue}, use shock-paper-run for crypto)",
            )
            return result

        session = classify_session_regime(
            now, asset_class=inst["asset_class"], trading_hours_mode=inst["trading_hours_mode"],
        )
        result.session_regime = session

        t0 = time.monotonic()
        try:
            ticker = bybit.fetch_ticker(sym)
        except Exception as exc:
            result.status = "fetch_failed"
            result.error = str(exc)
            self._persist_fetch_failure(conn, instrument=inst, obs_ts=now, session_regime=session, error=str(exc))
            self._progress(f"cycle={cycle} {idx}/{total} {canonical} FAIL error={exc} elapsed={time.monotonic()-t0:.2f}s")
            return result

        if ticker is None:
            result.status = "fetch_failed"
            result.error = "no_ticker"
            self._persist_fetch_failure(conn, instrument=inst, obs_ts=now, session_regime=session, error="no_ticker")
            self.stats.fetch_failed += 1
            self._progress(f"cycle={cycle} {idx}/{total} {canonical} FAIL no_ticker elapsed={time.monotonic()-t0:.2f}s")
            return result

        qq = evaluate_bybit_quote(ticker, poll_ts=now)
        ref = resolve_bybit_reference(index_price=ticker.index_price, mark_price=ticker.mark_price)
        result.last_price = ticker.last_price
        result.index_price = ref.price
        result.spread_bps = qq.spread_bps
        result.basis_bps = qq.basis_bps
        result.quote_age_sec = qq.quote_age_sec

        observe_status = "inserted" if qq.ok else "stale"
        self._persist_observation(
            conn, instrument=inst, obs_ts=now, ticker=ticker, session_regime=session,
            observe_status=observe_status, quote_quality_ok=qq.ok,
        )
        result.status = observe_status
        result.inserted = True

        self._progress(
            f"cycle={cycle} {idx}/{total} {canonical} OK last={ticker.last_price} "
            f"index={ref.price} spread_bps={qq.spread_bps} basis_bps={qq.basis_bps} "
            f"session={session} quote_age={qq.quote_age_sec:.1f}s "
            f"observation={observe_status} elapsed={time.monotonic()-t0:.2f}s",
        )
        return result

    def run_once(
        self,
        conn: Any,
        instruments: list[dict[str, Any]],
        bybit: BybitMarketClient,
        *,
        cycle: int,
    ) -> None:
        now = int(time.time())
        self.stats.polls += 1
        total = len(instruments)
        t_cycle = time.monotonic()
        self._progress(f"cycle={cycle} start instruments={total} mode={self.universe_mode}")

        for idx, inst in enumerate(instruments, start=1):
            if not inst.get("observe_enabled"):
                self.stats.skipped += 1
                continue
            try:
                self._observe_one(conn, cycle=cycle, idx=idx, total=total, inst=inst, bybit=bybit, now=now)
            except Exception as exc:
                self.stats.errors.append(f"{inst.get('canonical_asset')}: {exc}")
                self._progress(f"cycle={cycle} {idx}/{total} {inst.get('canonical_asset')} ERROR {exc}")

        conn.commit()
        elapsed = time.monotonic() - t_cycle
        self._progress(
            f"cycle={cycle} complete observations={self.stats.observations} "
            f"failed={self.stats.fetch_failed} stale={self.stats.stale_recorded} "
            f"elapsed={elapsed:.2f}s sleep={POLL_INTERVAL_SEC}s",
        )

    def run(self) -> ObserveStats:
        logging.basicConfig(level=logging.INFO, format="[observe] %(message)s", stream=sys.stdout, force=True)
        try:
            from bot.research.market_events.telegram_ops.startup_validation import (
                validate_telegram_config_at_startup,
            )
            validate_telegram_config_at_startup()
        except Exception as exc:
            logging.getLogger(__name__).debug("telegram startup validation skipped: %s", exc)

        signal.signal(signal.SIGINT, lambda *_: self.request_shutdown())
        signal.signal(signal.SIGTERM, lambda *_: self.request_shutdown())

        with market_events_connection() as conn:
            from bot.research.market_events.startup_lock import market_events_startup_lock

            with market_events_startup_lock():
                apply_migrations(conn)
                instruments, version = select_observe_universe(
                    conn, mode=self.universe_mode, explicit_symbols=self.explicit_symbols,
                )
            self._progress(
                f"startup universe={version} instruments={len(instruments)} "
                f"mode={self.universe_mode} (sequential poll, isolated errors)",
            )
            if not instruments:
                self._progress("no instruments in scope — run instrument-discover first")
                return self.stats

            bybit = BybitMarketClient()
            cycles = 0
            while not self._shutdown:
                cycles += 1
                try:
                    self.run_once(conn, instruments, bybit, cycle=cycles)
                except Exception as exc:
                    self.stats.errors.append(str(exc))
                    self._progress(f"cycle={cycles} fatal_error={exc}")
                if self.max_cycles and cycles >= self.max_cycles:
                    break
                if not self._shutdown:
                    time.sleep(POLL_INTERVAL_SEC)

        self._progress(
            f"complete polls={self.stats.polls} observations={self.stats.observations} "
            f"fetch_failed={self.stats.fetch_failed} stale={self.stats.stale_recorded}",
        )
        return self.stats


def run_observe(
    *,
    universe: str = "tradfi-observe",
    explicit_symbols: list[str] | None = None,
    max_cycles: int | None = None,
) -> ObserveStats:
    return ObservationRunner(
        universe_mode=universe,
        explicit_symbols=explicit_symbols,
        max_cycles=max_cycles,
    ).run()
