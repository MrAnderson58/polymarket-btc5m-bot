"""Multi-venue price feed — routes Binance (E.1) and Bybit (E.2) without changing E.1 feed."""

from __future__ import annotations

import logging
import time
from typing import Any

from bot.research.market_events.basis_monitor import observe_basis
from bot.research.market_events.price_feed import BinanceFuturesPriceFeed, PriceTick, SymbolPriceState
from bot.research.market_events.venue_bybit import BybitMarketClient

logger = logging.getLogger(__name__)


class MultiVenuePriceFeed:
    """Wraps BinanceFuturesPriceFeed for crypto; adds Bybit for TradFi instruments."""

    def __init__(
        self,
        instruments: list[dict[str, Any]],
        *,
        binance_feed: BinanceFuturesPriceFeed | None = None,
        bybit_client: BybitMarketClient | None = None,
    ) -> None:
        self._instruments = instruments
        self._binance = binance_feed or BinanceFuturesPriceFeed()
        self._bybit = bybit_client or BybitMarketClient()
        self._bybit_states: dict[str, SymbolPriceState] = {}
        self._ref_states: dict[str, SymbolPriceState] = {}
        self._basis_cache: dict[str, float | None] = {}
        self._instrument_map: dict[str, dict[str, Any]] = {
            r["canonical_asset"]: r for r in instruments
        }

    def get_state(self, symbol: str) -> SymbolPriceState | None:
        row = self._instrument_map.get(symbol.upper())
        if row and row["venue"] == "bybit_linear":
            return self._bybit_states.get(symbol.upper())
        return self._binance.get_state(symbol)

    def poll_universe(self, symbols: list[str], *, max_age_sec: int) -> dict[str, PriceTick]:
        out: dict[str, PriceTick] = {}
        now = int(time.time())
        for sym in symbols:
            row = self._instrument_map.get(sym.upper())
            if row and row["venue"] == "bybit_linear":
                tick = self._poll_bybit(sym, row, now_ts=now, max_age_sec=max_age_sec)
            else:
                tick = self._binance.poll_symbol(sym, now_ts=now)
                if tick:
                    self._binance.ensure_symbol(sym).append(tick, max_age_sec=max_age_sec)
            if tick:
                out[sym] = tick
        return out

    def _poll_bybit(
        self,
        canonical: str,
        row: dict[str, Any],
        *,
        now_ts: int,
        max_age_sec: int,
    ) -> PriceTick | None:
        venue_sym = row["venue_symbol"]
        ticker = self._bybit.fetch_ticker(venue_sym)
        if not ticker:
            return None
        ref = ticker.index_price or ticker.mark_price
        basis_obs = observe_basis(
            trade_price=ticker.last_price,
            reference_price=ref,
            prior_basis_bps=self._basis_cache.get(canonical),
            bid=ticker.bid,
            ask=ticker.ask,
        )
        self._basis_cache[canonical] = basis_obs.basis_bps
        if canonical not in self._bybit_states:
            self._bybit_states[canonical] = SymbolPriceState(symbol=canonical, pair=venue_sym)
        state = self._bybit_states[canonical]
        tick = PriceTick(ts=now_ts, price=ticker.last_price, volume=ticker.volume_24h)
        state.append(tick, max_age_sec=max_age_sec)
        state.last_price = ticker.last_price
        if ref is not None:
            if canonical not in self._ref_states:
                self._ref_states[canonical] = SymbolPriceState(symbol=f"{canonical}_REF", pair=venue_sym)
            ref_state = self._ref_states[canonical]
            ref_tick = PriceTick(ts=now_ts, price=ref, volume=0.0)
            ref_state.append(ref_tick, max_age_sec=max_age_sec)
        return tick

    def get_reference_return_pct(self, symbol: str, window_sec: int, now_ts: int) -> float | None:
        ref = self._ref_states.get(symbol.upper())
        if ref:
            return ref.return_over(window_sec, now_ts)
        return None

    def get_basis_bps(self, symbol: str) -> float | None:
        return self._basis_cache.get(symbol.upper())

    def get_reference_price(self, symbol: str) -> float | None:
        row = self._instrument_map.get(symbol.upper())
        if not row or row["venue"] != "bybit_linear":
            return self._binance.get_state(symbol.upper()).last_price if self._binance.get_state(symbol.upper()) else None
        ticker = self._bybit.fetch_ticker(row["venue_symbol"])
        return ticker.index_price if ticker else None
