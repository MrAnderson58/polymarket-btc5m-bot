"""BTC price helpers via Binance public API."""

from __future__ import annotations

import logging

import requests

from bot.config import BINANCE_API, BTC_SYMBOL

logger = logging.getLogger(__name__)


class BtcPriceError(Exception):
    """Raised when BTC price data cannot be fetched."""


def get_current_btc_price() -> float:
    """Return the latest BTC/USDT spot price from Binance."""
    try:
        response = requests.get(
            f"{BINANCE_API}/api/v3/ticker/price",
            params={"symbol": BTC_SYMBOL},
            timeout=5,
        )
        response.raise_for_status()
        price = float(response.json()["price"])
        if price <= 0:
            raise BtcPriceError("Binance returned non-positive BTC price")
        return price
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        raise BtcPriceError(f"Failed to fetch current BTC price: {exc}") from exc


def get_strike_price(window_start_ts: int) -> float:
    """
    Return the strike (opening) price for a 5-minute window.

    Uses the open of the Binance 5m candle that starts at window_start_ts.
    Polymarket resolves on Chainlink; Binance is a practical public proxy for research.
    """
    try:
        response = requests.get(
            f"{BINANCE_API}/api/v3/klines",
            params={
                "symbol": BTC_SYMBOL,
                "interval": "5m",
                "startTime": window_start_ts * 1000,
                "limit": 1,
            },
            timeout=5,
        )
        response.raise_for_status()
        candles = response.json()
        if not candles:
            raise BtcPriceError(f"No 5m candle for window start {window_start_ts}")
        strike = float(candles[0][1])
        if strike <= 0:
            raise BtcPriceError("Binance returned non-positive strike price")
        return strike
    except (requests.RequestException, IndexError, KeyError, TypeError, ValueError) as exc:
        raise BtcPriceError(f"Failed to fetch strike price: {exc}") from exc
