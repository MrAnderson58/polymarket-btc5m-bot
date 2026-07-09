"""Binance futures price feed for Phase E.1 paper shock detection."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import requests

from bot.research.futures.config import BINANCE_FUTURES_API

logger = logging.getLogger(__name__)


@dataclass
class PriceTick:
    ts: int
    price: float
    volume: float = 0.0


@dataclass
class SymbolPriceState:
    symbol: str
    pair: str
    ticks: deque[PriceTick] = field(default_factory=deque)
    last_price: float | None = None

    def append(self, tick: PriceTick, *, max_age_sec: int) -> None:
        self.ticks.append(tick)
        self.last_price = tick.price
        cutoff = tick.ts - max_age_sec
        while self.ticks and self.ticks[0].ts < cutoff:
            self.ticks.popleft()

    def return_over(self, window_sec: int, now_ts: int) -> float | None:
        if not self.ticks or self.last_price is None:
            return None
        target_ts = now_ts - window_sec
        ref_price = None
        for t in self.ticks:
            if t.ts >= target_ts:
                ref_price = t.price
                break
        if ref_price is None:
            ref_price = self.ticks[0].price
        if ref_price <= 0:
            return None
        return (self.last_price / ref_price - 1.0) * 100.0

    def volume_zscore(self, window_sec: int, now_ts: int) -> float | None:
        vols = [t.volume for t in self.ticks if t.ts >= now_ts - window_sec and t.volume > 0]
        if len(vols) < 5:
            return None
        mean = sum(vols) / len(vols)
        var = sum((v - mean) ** 2 for v in vols) / len(vols)
        if var <= 0:
            return 0.0
        latest = vols[-1]
        return (latest - mean) / (var ** 0.5)


class BinanceFuturesPriceFeed:
    """REST polling feed — no websocket in E.1."""

    def __init__(self, *, api_base: str = BINANCE_FUTURES_API, timeout: float = 5.0) -> None:
        self._api = api_base.rstrip("/")
        self._timeout = timeout
        self._states: dict[str, SymbolPriceState] = {}

    def _pair(self, symbol: str) -> str:
        s = symbol.upper().replace("USDT", "")
        return f"{s}USDT"

    def ensure_symbol(self, symbol: str) -> SymbolPriceState:
        if symbol not in self._states:
            pair = self._pair(symbol)
            self._states[symbol] = SymbolPriceState(symbol=symbol, pair=pair)
        return self._states[symbol]

    def poll_symbol(self, symbol: str, *, now_ts: int | None = None) -> PriceTick | None:
        pair = self._pair(symbol)
        now = now_ts or int(time.time())
        try:
            r = requests.get(
                f"{self._api}/fapi/v1/ticker/24hr",
                params={"symbol": pair},
                timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
            price = float(data["lastPrice"])
            vol = float(data.get("volume", 0) or 0)
        except Exception as exc:
            logger.warning("price poll failed %s: %s", pair, exc)
            return None
        tick = PriceTick(ts=now, price=price, volume=vol)
        return tick

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
        pair = self._pair(symbol)
        try:
            r = requests.get(
                f"{self._api}/fapi/v1/premiumIndex",
                params={"symbol": pair},
                timeout=self._timeout,
            )
            r.raise_for_status()
            return float(r.json().get("lastFundingRate", 0) or 0)
        except Exception:
            return None
