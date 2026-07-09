"""Abstract market data provider — Binance default; extensible to Bybit/OKX/Hyperliquid."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import requests

from bot.research.futures.config import BINANCE_FUTURES_API, BINANCE_SPOT_API

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8
DEFAULT_CONNECT_TIMEOUT = 5
DEFAULT_READ_TIMEOUT = 15
DEFAULT_RETRIES = 2
MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 0.5


def symbol_pair(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("USDT", "")
    return f"{s}USDT"


class MarketDataProvider(ABC):
    @abstractmethod
    def fetch_spot_klines(
        self, pair: str, interval: str, end_ts: int, *, limit: int = 500,
    ) -> list[list]:
        ...

    @abstractmethod
    def fetch_futures_klines(
        self, pair: str, interval: str, end_ts: int, *, limit: int = 500,
    ) -> list[list]:
        ...

    @abstractmethod
    def fetch_funding_rate(self, pair: str, end_ts: int) -> tuple[float | None, str]:
        ...

    def symbol_available(self, pair: str, end_ts: int) -> bool:
        candles = self.fetch_spot_klines(pair, "1m", end_ts, limit=5)
        if candles:
            return True
        return bool(self.fetch_futures_klines(pair, "1m", end_ts, limit=5))


class BinanceMarketProvider(MarketDataProvider):
    """Public Binance spot + USDT-M futures endpoints (no API key)."""

    def __init__(
        self,
        *,
        spot_api: str = BINANCE_SPOT_API,
        futures_api: str = BINANCE_FUTURES_API,
        timeout: float | tuple[float, float] | None = None,
        retries: int = DEFAULT_RETRIES,
        session: requests.Session | None = None,
    ) -> None:
        self.spot_api = spot_api.rstrip("/")
        self.futures_api = futures_api.rstrip("/")
        if timeout is None:
            timeout = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)
        self.timeout = timeout
        self.retries = min(retries, MAX_RETRIES)
        self._session = session or requests.Session()

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 400:
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
        logger.debug("Binance request failed %s: %s", url, last_exc)
        return None

    def fetch_spot_klines(
        self, pair: str, interval: str, end_ts: int, *, limit: int = 500,
    ) -> list[list]:
        data = self._get(
            f"{self.spot_api}/api/v3/klines",
            {"symbol": pair, "interval": interval, "endTime": end_ts * 1000, "limit": limit},
        )
        return data or []

    def fetch_futures_klines(
        self, pair: str, interval: str, end_ts: int, *, limit: int = 500,
    ) -> list[list]:
        data = self._get(
            f"{self.futures_api}/fapi/v1/klines",
            {"symbol": pair, "interval": interval, "endTime": end_ts * 1000, "limit": limit},
        )
        return data or []

    def fetch_funding_rate(self, pair: str, end_ts: int) -> tuple[float | None, str]:
        data = self._get(
            f"{self.futures_api}/fapi/v1/fundingRate",
            {"symbol": pair, "endTime": end_ts * 1000, "limit": 1},
        )
        if not data:
            return None, "unavailable"
        fr_ts = int(data[0]["fundingTime"] // 1000)
        if fr_ts > end_ts:
            return None, "future_funding"
        return float(data[0]["fundingRate"]), "available"
