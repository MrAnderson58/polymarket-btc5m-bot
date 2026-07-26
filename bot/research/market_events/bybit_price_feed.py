"""Bybit linear price feed — drop-in poll interface for shock-paper-core.

Mirrors BinanceFuturesPriceFeed methods used by ShockPaperRunner / detectors.
Uses existing BybitMarketClient. Optional OKX last-price fallback on Bybit miss.
"""

from __future__ import annotations

import logging
import time

from bot.research.market_events.price_feed import PriceTick, SymbolPriceState
from bot.research.market_events.venue_bybit import BybitMarketClient

logger = logging.getLogger(__name__)


class BybitPriceFeed:
    """REST polling feed via Bybit V5 linear tickers (same surface as Binance feed)."""

    def __init__(
        self,
        *,
        client: BybitMarketClient | None = None,
        okx_fallback: bool = True,
        timeout: float = 5.0,
    ) -> None:
        self._bybit = client or BybitMarketClient(read_timeout=timeout, connect_timeout=min(3.0, timeout))
        self._okx_fallback = okx_fallback
        self._states: dict[str, SymbolPriceState] = {}

    def _pair(self, symbol: str) -> str:
        s = symbol.upper().replace("USDT", "")
        return f"{s}USDT"

    def ensure_symbol(self, symbol: str) -> SymbolPriceState:
        if symbol not in self._states:
            pair = self._pair(symbol)
            self._states[symbol] = SymbolPriceState(symbol=symbol, pair=pair)
        return self._states[symbol]

    def _tick_from_okx(self, symbol: str, *, now_ts: int) -> PriceTick | None:
        try:
            from bot.research.market_events.signal_intelligence.okx_client import (
                fetch_ticker_metrics,
            )
        except Exception:
            return None
        metrics = fetch_ticker_metrics(symbol)
        if not metrics:
            return None
        price = float(metrics.get("last_price") or 0)
        if price <= 0:
            return None
        vol = float(metrics.get("volume_24h") or 0)
        logger.info("price poll OKX fallback %s last=%.6g", self._pair(symbol), price)
        return PriceTick(ts=now_ts, price=price, volume=vol)

    def poll_symbol(self, symbol: str, *, now_ts: int | None = None) -> PriceTick | None:
        pair = self._pair(symbol)
        now = now_ts or int(time.time())
        try:
            ticker = self._bybit.fetch_ticker(pair)
        except Exception as exc:
            logger.warning("price poll failed %s (bybit): %s", pair, exc)
            ticker = None
        if ticker is not None and ticker.last_price > 0:
            return PriceTick(
                ts=now,
                price=float(ticker.last_price),
                volume=float(ticker.volume_24h or 0),
            )
        if self._okx_fallback:
            tick = self._tick_from_okx(symbol, now_ts=now)
            if tick is not None:
                return tick
        logger.warning("price poll failed %s: bybit miss%s", pair, " + okx miss" if self._okx_fallback else "")
        return None

    def poll_universe(self, symbols: list[str], *, max_age_sec: int) -> dict[str, PriceTick]:
        out: dict[str, PriceTick] = {}
        now = int(time.time())
        for sym in symbols:
            tick = self.poll_symbol(sym, now_ts=now)
            if tick is None:
                continue
            state = self.ensure_symbol(sym)
            state.append(tick, max_age_sec=max_age_sec)
            out[sym] = tick
        return out

    def get_state(self, symbol: str) -> SymbolPriceState | None:
        return self._states.get(symbol)

    def fetch_funding(self, symbol: str) -> float | None:
        """Best-effort funding from Bybit mark/index path — unused by core detectors."""
        pair = self._pair(symbol)
        ticker = self._bybit.fetch_ticker(pair)
        if ticker is None:
            return None
        # Bybit ticker payload does not always expose funding on this client;
        # keep interface parity without calling Binance.
        return None
