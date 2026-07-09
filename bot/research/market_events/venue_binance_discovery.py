"""Binance futures instrument discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

from bot.research.futures.config import BINANCE_FUTURES_API

logger = logging.getLogger(__name__)


@dataclass
class BinanceInstrument:
    venue_symbol: str
    base_asset: str
    quote_asset: str
    contract_type: str
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


class BinanceDiscoveryClient:
    def __init__(self, *, api_base: str = BINANCE_FUTURES_API, timeout: float = 8.0) -> None:
        self._api = api_base.rstrip("/")
        self._timeout = timeout

    def fetch_perpetual_instruments(self) -> list[BinanceInstrument]:
        try:
            r = requests.get(f"{self._api}/fapi/v1/exchangeInfo", timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("Binance exchangeInfo failed: %s", exc)
            return []
        out: list[BinanceInstrument] = []
        for sym in data.get("symbols") or []:
            if sym.get("contractType") != "PERPETUAL":
                continue
            if sym.get("quoteAsset") != "USDT":
                continue
            if sym.get("status") != "TRADING":
                continue
            out.append(BinanceInstrument(
                venue_symbol=str(sym["symbol"]),
                base_asset=str(sym["baseAsset"]),
                quote_asset=str(sym["quoteAsset"]),
                contract_type=str(sym.get("contractType") or ""),
                status=str(sym.get("status") or ""),
                raw=sym,
            ))
        return out

    def fetch_ticker_24h(self, symbol: str) -> dict[str, Any] | None:
        try:
            r = requests.get(
                f"{self._api}/fapi/v1/ticker/24hr",
                params={"symbol": symbol},
                timeout=self._timeout,
            )
            r.raise_for_status()
            return r.json()
        except Exception:
            return None
