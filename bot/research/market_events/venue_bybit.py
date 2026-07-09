"""Bybit V5 market data — instrument discovery and price polling."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

BYBIT_API = "https://api.bybit.com"
DEFAULT_TIMEOUT = 8.0
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 5.0
MAX_FETCH_RETRIES = 1


@dataclass
class BybitInstrument:
    venue_symbol: str
    base_coin: str
    quote_coin: str
    contract_type: str
    symbol_type: str
    status: str
    launch_time: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BybitTicker:
    symbol: str
    last_price: float
    index_price: float | None
    mark_price: float | None
    bid: float | None
    ask: float | None
    volume_24h: float
    turnover_24h: float
    ts: int


class BybitMarketClient:
    def __init__(
        self,
        *,
        api_base: str = BYBIT_API,
        timeout: float = DEFAULT_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        self._api = api_base.rstrip("/")
        self._timeout = (connect_timeout, read_timeout) if timeout == DEFAULT_TIMEOUT else timeout
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        timeout = (self._connect_timeout, self._read_timeout)
        r = requests.get(f"{self._api}{path}", params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if int(data.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")
        return data.get("result") or {}

    def fetch_ticker(self, symbol: str, *, category: str = "linear") -> BybitTicker | None:
        last_exc: Exception | None = None
        for attempt in range(MAX_FETCH_RETRIES + 1):
            try:
                result = self._get("/v5/market/tickers", {"category": category, "symbol": symbol})
                rows = result.get("list") or []
                if not rows:
                    return None
                row = rows[0]
                bid = float(row["bid1Price"]) if row.get("bid1Price") else None
                ask = float(row["ask1Price"]) if row.get("ask1Price") else None
                return BybitTicker(
                    symbol=symbol,
                    last_price=float(row["lastPrice"]),
                    index_price=float(row["indexPrice"]) if row.get("indexPrice") else None,
                    mark_price=float(row["markPrice"]) if row.get("markPrice") else None,
                    bid=bid,
                    ask=ask,
                    volume_24h=float(row.get("volume24h") or 0),
                    turnover_24h=float(row.get("turnover24h") or 0),
                    ts=int(time.time()),
                )
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_FETCH_RETRIES:
                    time.sleep(0.2)
        logger.warning("Bybit ticker failed %s: %s", symbol, last_exc)
        return None

    def fetch_instruments(
        self,
        *,
        category: str = "linear",
        symbol_type: str | None = None,
        limit: int = 1000,
    ) -> list[BybitInstrument]:
        params: dict[str, Any] = {"category": category, "limit": limit}
        if symbol_type:
            params["symbolType"] = symbol_type
        out: list[BybitInstrument] = []
        cursor: str | None = None
        while True:
            if cursor:
                params["cursor"] = cursor
            result = self._get("/v5/market/instruments-info", params)
            for row in result.get("list") or []:
                if row.get("status") not in ("Trading", "PreLaunch"):
                    continue
                out.append(BybitInstrument(
                    venue_symbol=str(row["symbol"]),
                    base_coin=str(row.get("baseCoin") or ""),
                    quote_coin=str(row.get("quoteCoin") or ""),
                    contract_type=str(row.get("contractType") or ""),
                    symbol_type=str(row.get("symbolType") or symbol_type or ""),
                    status=str(row.get("status") or ""),
                    launch_time=int(row["launchTime"]) // 1000 if row.get("launchTime") else None,
                    raw=row,
                ))
            cursor = result.get("nextPageCursor")
            if not cursor:
                break
        return out

    def fetch_tickers_batch(self, symbols: list[str], *, category: str = "linear") -> dict[str, BybitTicker]:
        out: dict[str, BybitTicker] = {}
        for sym in symbols:
            t = self.fetch_ticker(sym, category=category)
            if t:
                out[sym] = t
        return out
