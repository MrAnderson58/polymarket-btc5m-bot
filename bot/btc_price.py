"""BTC price helpers with Binance → Coinbase → Kraken fallback."""

from __future__ import annotations

import logging
import time

import requests

from bot.config import BINANCE_API, BTC_PRICE_CACHE_TTL_SEC, BTC_SYMBOL, POLL_INTERVAL_SEC

logger = logging.getLogger(__name__)

COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker"
REQUEST_TIMEOUT_SEC = 5
FIVE_MINUTES_SEC = 300

_strike_cache: dict[int, float] = {}
_btc_price_cache: tuple[float, float] | None = None


def _btc_price_cache_ttl_sec() -> float:
    """Ensure cache covers at least one main-loop poll interval."""
    return max(BTC_PRICE_CACHE_TTL_SEC, POLL_INTERVAL_SEC)


def clear_price_caches() -> None:
    """Reset in-memory price caches (for tests)."""
    global _btc_price_cache
    _strike_cache.clear()
    _btc_price_cache = None


class BtcPriceError(Exception):
    """Raised when BTC price data cannot be fetched."""


def _round_to_5m_window_start(window_start_ts: int) -> int:
    return (window_start_ts // FIVE_MINUTES_SEC) * FIVE_MINUTES_SEC


def _fetch_binance_price() -> float:
    logger.info("BTC fetch | source=binance")
    response = requests.get(
        f"{BINANCE_API}/api/v3/ticker/price",
        params={"symbol": BTC_SYMBOL},
        timeout=REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    price = float(response.json()["price"])
    if price <= 0:
        raise BtcPriceError("Binance returned non-positive BTC price")
    return price


def _fetch_coinbase_price() -> float:
    logger.info("BTC fetch | source=coinbase")
    response = requests.get(COINBASE_SPOT_URL, timeout=REQUEST_TIMEOUT_SEC)
    response.raise_for_status()
    price = float(response.json()["data"]["amount"])
    if price <= 0:
        raise BtcPriceError("Coinbase returned non-positive BTC price")
    return price


def _fetch_kraken_price() -> float:
    logger.info("BTC fetch | source=kraken")
    response = requests.get(
        KRAKEN_TICKER_URL,
        params={"pair": "XBTUSD"},
        timeout=REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise BtcPriceError(f"Kraken error: {payload['error']}")
    result = payload.get("result") or {}
    if not result:
        raise BtcPriceError("Kraken returned empty ticker result")
    ticker = next(iter(result.values()))
    price = float(ticker["c"][0])
    if price <= 0:
        raise BtcPriceError("Kraken returned non-positive BTC price")
    return price


def _fetch_current_btc_price() -> float:
    """Fetch BTC spot price without cache."""
    errors: list[str] = []

    try:
        price = _fetch_binance_price()
        return price
    except (requests.RequestException, KeyError, TypeError, ValueError, BtcPriceError) as exc:
        errors.append(f"binance: {exc}")
        logger.warning("Binance unavailable, falling back to Coinbase")

    try:
        price = _fetch_coinbase_price()
        return price
    except (requests.RequestException, KeyError, TypeError, ValueError, BtcPriceError) as exc:
        errors.append(f"coinbase: {exc}")
        logger.warning("Coinbase unavailable, falling back to Kraken")

    try:
        price = _fetch_kraken_price()
        return price
    except (requests.RequestException, KeyError, TypeError, ValueError, BtcPriceError) as exc:
        errors.append(f"kraken: {exc}")

    raise BtcPriceError(
        "Failed to fetch current BTC price from all sources: " + "; ".join(errors)
    )


def get_current_btc_price() -> float:
    """Return the latest BTC spot price from Binance, Coinbase, or Kraken."""
    global _btc_price_cache

    now = time.monotonic()
    ttl = _btc_price_cache_ttl_sec()
    if _btc_price_cache is not None:
        cached_price, fetched_at = _btc_price_cache
        age = now - fetched_at
        if age <= ttl:
            logger.info("BTC cache HIT | age=%.2fs ttl=%.2fs", age, ttl)
            return cached_price

    logger.info("BTC cache MISS | ttl=%.2fs", ttl)
    price = _fetch_current_btc_price()
    _btc_price_cache = (price, now)
    return price


def _fetch_binance_strike(window_start_ts: int) -> float:
    response = requests.get(
        f"{BINANCE_API}/api/v3/klines",
        params={
            "symbol": BTC_SYMBOL,
            "interval": "5m",
            "startTime": window_start_ts * 1000,
            "limit": 1,
        },
        timeout=REQUEST_TIMEOUT_SEC,
    )
    response.raise_for_status()
    candles = response.json()
    if not candles:
        raise BtcPriceError(f"No 5m candle for window start {window_start_ts}")
    strike = float(candles[0][1])
    if strike <= 0:
        raise BtcPriceError("Binance returned non-positive strike price")
    return strike


def _parse_coinbase_candle_open(candles: list, window_start_ts: int) -> float:
    logger.info(
        "Looking for candle_ts=%s, received=%s",
        window_start_ts,
        [int(c[0]) for c in candles],
    )
    if not candles:
        raise BtcPriceError(f"No Coinbase 5m candle for window start {window_start_ts}")
    for candle in candles:
        candle_ts = int(candle[0])
        if candle_ts == window_start_ts:
            strike = float(candle[3])
            if strike <= 0:
                raise BtcPriceError("Coinbase returned non-positive strike price")
            return strike
    raise BtcPriceError(
        f"No Coinbase candle matching window start {window_start_ts}"
    )


def _fetch_coinbase_strike(window_start_ts: int) -> float:
    ranges = (
        (window_start_ts, window_start_ts + FIVE_MINUTES_SEC),
        (window_start_ts - FIVE_MINUTES_SEC, window_start_ts + FIVE_MINUTES_SEC),
        (window_start_ts - 3600, window_start_ts + FIVE_MINUTES_SEC),
    )
    last_error: Exception | None = None

    for start_ts, end_ts in ranges:
        try:
            response = requests.get(
                COINBASE_CANDLES_URL,
                params={
                    "granularity": FIVE_MINUTES_SEC,
                    "start": str(start_ts),
                    "end": str(end_ts),
                },
                timeout=REQUEST_TIMEOUT_SEC,
            )
            response.raise_for_status()
            payload = response.json()
            logger.info(
                "Coinbase candles request | url=%s start_ts=%s end_ts=%s window=%s",
                response.url,
                start_ts,
                end_ts,
                window_start_ts,
            )
            logger.info(
                "Coinbase candles | window=%s returned=%s",
                window_start_ts,
                payload[:5] if isinstance(payload, list) else payload,
            )
            return _parse_coinbase_candle_open(payload, window_start_ts)
        except (
            requests.RequestException,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            BtcPriceError,
        ) as exc:
            last_error = exc

    raise BtcPriceError(
        f"Failed to fetch Coinbase strike for window start {window_start_ts}: {last_error}"
    )


def _prune_strike_cache(keep_window: int) -> None:
    stale_before = keep_window - FIVE_MINUTES_SEC
    for key in list(_strike_cache):
        if key < stale_before:
            del _strike_cache[key]


def _resolve_strike_price(aligned_start: int) -> float:
    now = int(time.time())
    logger.info(
        "STRIKE DEBUG | now=%s window_start=%s delta=%s",
        now,
        aligned_start,
        aligned_start - now,
    )
    errors: list[str] = []

    try:
        strike = _fetch_binance_strike(aligned_start)
        logger.info("Strike source=binance")
        return strike
    except (requests.RequestException, IndexError, KeyError, TypeError, ValueError, BtcPriceError) as exc:
        errors.append(f"binance: {exc}")
        logger.warning("Binance unavailable for strike, falling back to Coinbase")

    try:
        strike = _fetch_coinbase_strike(aligned_start)
        logger.info("Strike source=coinbase")
        return strike
    except (requests.RequestException, IndexError, KeyError, TypeError, ValueError, BtcPriceError) as exc:
        errors.append(f"coinbase: {exc}")

    seconds_into_window = now - aligned_start
    if 0 <= seconds_into_window < FIVE_MINUTES_SEC:
        try:
            strike = get_current_btc_price()
            logger.warning(
                "Strike source=spot | window=%s seconds_into_window=%s "
                "(exchange 5m candle not yet published)",
                aligned_start,
                seconds_into_window,
            )
            return strike
        except BtcPriceError as exc:
            errors.append(f"spot: {exc}")

    raise BtcPriceError(
        f"Failed to fetch strike price for window {aligned_start}: " + "; ".join(errors)
    )


def get_strike_price(window_start_ts: int) -> float:
    """
    Return the strike (opening) price for a 5-minute window.

    Uses the open of the 5m candle that starts at window_start_ts.
    Cached for the duration of the window.
    """
    aligned_start = _round_to_5m_window_start(window_start_ts)

    cached = _strike_cache.get(aligned_start)
    if cached is not None:
        logger.info("Strike cache HIT | window=%s", aligned_start)
        return cached

    logger.info("Strike cache MISS | window=%s", aligned_start)
    strike = _resolve_strike_price(aligned_start)
    _strike_cache[aligned_start] = strike
    _prune_strike_cache(aligned_start)
    return strike
