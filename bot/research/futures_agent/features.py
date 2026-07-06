"""Market feature engineering at signal time T — strict no look-ahead."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures.config import RETURN_WINDOWS
from bot.research.futures_agent.config import (
    DATA_QUALITY_API_UNAVAILABLE,
    DATA_QUALITY_COMPLETE,
    DATA_QUALITY_PARTIAL,
    DATA_QUALITY_STALE,
    DATA_QUALITY_SYMBOL_UNAVAILABLE,
)
from bot.research.futures_agent.market_provider import MarketDataProvider, symbol_pair


def candles_at_or_before(candles: list[list], ts: int) -> list[list]:
    return [c for c in candles if int(c[0] // 1000) <= ts]


def close_at(candles: list[list], ts: int) -> float | None:
    eligible = candles_at_or_before(candles, ts)
    if not eligible:
        return None
    return float(eligible[-1][4])


def return_over(candles: list[list], ts: int, window_sec: int) -> float | None:
    p_now = close_at(candles, ts)
    p_then = close_at(candles, ts - window_sec)
    if p_now is None or p_then is None or p_then == 0:
        return None
    return (p_now - p_then) / p_then * 100.0


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1 - k)
    return result


def atr(candles: list[list], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        h, l, pc = float(candles[i][2]), float(candles[i][3]), float(candles[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def realized_volatility(candles: list[list], ts: int, window: int = 30) -> float | None:
    window_candles = candles_at_or_before(candles, ts)[-window - 1:]
    if len(window_candles) < 3:
        return None
    rets: list[float] = []
    for i in range(1, len(window_candles)):
        prev, cur = float(window_candles[i - 1][4]), float(window_candles[i][4])
        if prev > 0:
            rets.append((cur - prev) / prev * 100.0)
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets)


def volume_ratio(candles: list[list], ts: int, window: int = 20) -> float | None:
    window_candles = candles_at_or_before(candles, ts)[-window:]
    if len(window_candles) < 2:
        return None
    vols = [float(c[5]) for c in window_candles]
    avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
    if avg == 0:
        return None
    return vols[-1] / avg


def minute_returns(candles: list[list], ts: int, n: int) -> list[float]:
    window = candles_at_or_before(candles, ts)[-n - 1:]
    rets: list[float] = []
    for i in range(1, len(window)):
        prev, cur = float(window[i - 1][4]), float(window[i][4])
        if prev > 0:
            rets.append((cur - prev) / prev)
    return rets


def correlation_beta(
    alt_rets: list[float], btc_rets: list[float],
) -> tuple[float | None, float | None]:
    n = min(len(alt_rets), len(btc_rets))
    if n < 5:
        return None, None
    a = alt_rets[-n:]
    b = btc_rets[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
    var_b = sum((x - mean_b) ** 2 for x in b) / n
    if var_b == 0:
        return None, None
    beta = cov / var_b
    std_a = math.sqrt(sum((x - mean_a) ** 2 for x in a) / n)
    std_b = math.sqrt(var_b)
    if std_a == 0 or std_b == 0:
        return None, beta
    corr = cov / (std_a * std_b)
    return corr, beta


def trend_label(return_pct: float | None) -> str | None:
    if return_pct is None:
        return None
    if return_pct > 0.15:
        return "UP"
    if return_pct < -0.15:
        return "DOWN"
    return "FLAT"


@dataclass
class AssetSnapshot:
    symbol: str
    pair: str
    snapshot_ts: int
    exchange: str = "binance"
    spot_price: float | None = None
    futures_price: float | None = None
    return_1m: float | None = None
    return_5m: float | None = None
    return_15m: float | None = None
    return_30m: float | None = None
    return_1h: float | None = None
    return_4h: float | None = None
    return_24h: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    ema_slope: float | None = None
    atr: float | None = None
    realized_vol: float | None = None
    distance_from_local_high: float | None = None
    distance_from_local_low: float | None = None
    volume_ratio: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    basis: float | None = None
    data_quality: str = DATA_QUALITY_PARTIAL
    raw_metadata_json: dict[str, Any] = field(default_factory=dict)
    minute_returns_1m: list[float] = field(default_factory=list)


def build_asset_snapshot(
    provider: MarketDataProvider,
    symbol: str,
    ts: int,
    *,
    include_funding: bool = True,
) -> AssetSnapshot:
    pair = symbol_pair(symbol)
    snap = AssetSnapshot(symbol=symbol.upper().replace("USDT", ""), pair=pair, snapshot_ts=ts)

    if not provider.symbol_available(pair, ts):
        snap.data_quality = DATA_QUALITY_SYMBOL_UNAVAILABLE
        return snap

    spot_1m = provider.fetch_spot_klines(pair, "1m", ts, limit=1500)
    fut_1m = provider.fetch_futures_klines(pair, "1m", ts, limit=1500)

    if not spot_1m and not fut_1m:
        snap.data_quality = DATA_QUALITY_API_UNAVAILABLE
        return snap

    use = spot_1m if spot_1m else fut_1m
    snap.spot_price = close_at(spot_1m, ts) if spot_1m else None
    snap.futures_price = close_at(fut_1m, ts) if fut_1m else close_at(use, ts)

    for label, sec in RETURN_WINDOWS.items():
        val = return_over(use, ts, sec)
        setattr(snap, f"return_{label}", val)

    closes = [float(c[4]) for c in candles_at_or_before(use, ts)]
    price = snap.futures_price or snap.spot_price
    if len(closes) >= 21:
        snap.ema_fast = ema(closes[-60:], 9)
        snap.ema_slow = ema(closes[-120:], 21)
        if snap.ema_fast is not None and snap.ema_slow is not None and price:
            snap.ema_slope = (snap.ema_fast - snap.ema_slow) / price * 100.0

    window = candles_at_or_before(use, ts)[-60:]
    if window and price:
        highs = [float(c[2]) for c in window]
        lows = [float(c[3]) for c in window]
        snap.distance_from_local_high = (price - max(highs)) / price * 100.0
        snap.distance_from_local_low = (price - min(lows)) / price * 100.0

    snap.atr = atr(candles_at_or_before(use, ts)[-30:])
    snap.realized_vol = realized_volatility(use, ts)
    snap.volume_ratio = volume_ratio(use, ts)
    snap.minute_returns_1m = minute_returns(use, ts, 60)

    if include_funding and pair != "BTCUSDT":
        funding, cov = provider.fetch_funding_rate(pair, ts)
        snap.funding_rate = funding
        snap.raw_metadata_json["funding_coverage"] = cov

    if snap.spot_price and snap.futures_price:
        snap.basis = snap.futures_price - snap.spot_price

    snap.raw_metadata_json["acceleration_5m"] = _acceleration(snap.return_5m, snap.return_15m)
    snap.raw_metadata_json["momentum_consistency"] = _momentum_consistency(snap)

    available = sum(
        1 for k in RETURN_WINDOWS if getattr(snap, f"return_{k}") is not None
    )
    if available >= 5 and price is not None:
        snap.data_quality = DATA_QUALITY_COMPLETE
    elif available > 0:
        snap.data_quality = DATA_QUALITY_PARTIAL
    else:
        snap.data_quality = DATA_QUALITY_STALE

    return snap


def _acceleration(ret_short: float | None, ret_long: float | None) -> float | None:
    if ret_short is None or ret_long is None:
        return None
    return ret_short - ret_long / 3.0


def _momentum_consistency(snap: AssetSnapshot) -> str | None:
    signs: list[int] = []
    for label in ("5m", "15m", "1h"):
        val = getattr(snap, f"return_{label}")
        if val is None:
            continue
        signs.append(1 if val > 0 else (-1 if val < 0 else 0))
    if not signs:
        return None
    if all(s > 0 for s in signs):
        return "bullish_aligned"
    if all(s < 0 for s in signs):
        return "bearish_aligned"
    return "mixed"
