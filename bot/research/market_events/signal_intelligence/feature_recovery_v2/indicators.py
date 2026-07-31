"""Signal Feature Recovery V2 — real candle indicators (no stubs)."""

from __future__ import annotations

import math
from typing import Sequence

from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    compute_atr,
    compute_ema,
    compute_vwap,
    pct_distance,
)


def compute_rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder RSI on close series (oldest→newest)."""
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = float(closes[i]) - float(closes[i - 1])
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        d = float(closes[i]) - float(closes[i - 1])
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 4)


def compute_ema_series(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if not values:
        return out
    k = 2.0 / (period + 1)
    if len(values) < period:
        # seed with running mean
        s = 0.0
        for i, v in enumerate(values):
            s += float(v)
            out[i] = s / (i + 1)
        return out
    seed = sum(float(values[i]) for i in range(period)) / period
    out[period - 1] = seed
    ema = seed
    for i in range(period, len(values)):
        ema = float(values[i]) * k + ema * (1 - k)
        out[i] = ema
    return out


def compute_macd(
    closes: Sequence[float],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float | None, float | None, float | None]:
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = compute_ema_series(closes, fast)
    ema_slow = compute_ema_series(closes, slow)
    macd_line: list[float] = []
    for a, b in zip(ema_fast, ema_slow):
        if a is None or b is None:
            continue
        macd_line.append(float(a) - float(b))
    if len(macd_line) < signal:
        return None, None, None
    signal_series = compute_ema_series(macd_line, signal)
    macd_v = macd_line[-1]
    sig_v = signal_series[-1]
    if sig_v is None:
        return round(macd_v, 8), None, None
    hist = macd_v - float(sig_v)
    return round(macd_v, 8), round(float(sig_v), 8), round(hist, 8)


def compute_bollinger(
    closes: Sequence[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return mid, upper, lower, %b."""
    if len(closes) < period:
        return None, None, None, None
    window = [float(x) for x in closes[-period:]]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(var)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = upper - lower
    pct_b = None if width <= 1e-12 else (float(closes[-1]) - lower) / width
    return round(mid, 8), round(upper, 8), round(lower, 8), (
        None if pct_b is None else round(pct_b, 4)
    )


def compute_adx(bars: Sequence[CandleBar], period: int = 14) -> float | None:
    if len(bars) < period + 2:
        return None
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, len(bars)):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None

    def wilder_smooth(vals: list[float], n: int) -> list[float]:
        out: list[float] = []
        s = sum(vals[:n])
        out.append(s)
        for i in range(n, len(vals)):
            s = s - (s / n) + vals[i]
            out.append(s)
        return out

    atr_s = wilder_smooth(trs, period)
    p_s = wilder_smooth(plus_dm, period)
    m_s = wilder_smooth(minus_dm, period)
    dx_vals: list[float] = []
    for a, p, m in zip(atr_s, p_s, m_s):
        if a <= 1e-12:
            continue
        plus_di = 100.0 * p / a
        minus_di = 100.0 * m / a
        denom = plus_di + minus_di
        if denom <= 1e-12:
            dx_vals.append(0.0)
        else:
            dx_vals.append(100.0 * abs(plus_di - minus_di) / denom)
    if len(dx_vals) < period:
        # average available
        if not dx_vals:
            return None
        return round(sum(dx_vals) / len(dx_vals), 4)
    adx = sum(dx_vals[:period]) / period
    for i in range(period, len(dx_vals)):
        adx = (adx * (period - 1) + dx_vals[i]) / period
    return round(adx, 4)


def compute_stochastic(
    bars: Sequence[CandleBar],
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[float | None, float | None]:
    if len(bars) < k_period:
        return None, None
    window = bars[-k_period:]
    hh = max(b.high for b in window)
    ll = min(b.low for b in window)
    if hh - ll <= 1e-12:
        k = 50.0
    else:
        k = 100.0 * (bars[-1].close - ll) / (hh - ll)
    # %D = SMA of last d %K approximations
    ks: list[float] = []
    for i in range(k_period - 1, len(bars)):
        w = bars[i - k_period + 1: i + 1]
        h = max(b.high for b in w)
        l = min(b.low for b in w)
        if h - l <= 1e-12:
            ks.append(50.0)
        else:
            ks.append(100.0 * (bars[i].close - l) / (h - l))
    if len(ks) < d_period:
        return round(k, 4), None
    d = sum(ks[-d_period:]) / d_period
    return round(k, 4), round(d, 4)


def compute_slope(values: Sequence[float], lookback: int = 20) -> float | None:
    """Normalized linear slope of closes over lookback (pct per bar)."""
    if len(values) < lookback:
        return None
    ys = [float(v) for v in values[-lookback:]]
    n = len(ys)
    xs = list(range(n))
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-12 or abs(my) <= 1e-12:
        return 0.0
    slope = num / den
    return round(100.0 * slope / my, 6)


def compute_trend_score(
    *,
    ema20: float | None,
    ema50: float | None,
    ema200: float | None,
    adx: float | None,
    slope: float | None,
) -> float | None:
    """Signed trend in [-1, 1] from EMA stack + ADX + slope."""
    if ema20 is None or ema50 is None:
        return None
    score = 0.0
    w = 0.0
    if ema20 > ema50:
        score += 0.35
    elif ema20 < ema50:
        score -= 0.35
    w += 0.35
    if ema200 is not None:
        if ema50 > ema200:
            score += 0.25
        elif ema50 < ema200:
            score -= 0.25
        w += 0.25
    if slope is not None:
        score += max(-0.25, min(0.25, slope / 2.0))
        w += 0.25
    if adx is not None:
        # amplify by trend strength
        strength = max(0.0, min(1.0, (adx - 15.0) / 35.0))
        score *= 0.5 + 0.5 * strength
        w += 0.15
    if w <= 0:
        return None
    return round(max(-1.0, min(1.0, score)), 4)


def atr_pct(atr: float | None, price: float | None) -> float | None:
    if atr is None or price is None or abs(price) < 1e-12:
        return None
    return round(100.0 * float(atr) / float(price), 6)


def indicators_from_bars(
    bars: list[CandleBar],
    *,
    entry: float | None = None,
) -> dict[str, float | None]:
    """Full indicator pack from OHLCV bars (oldest→newest)."""
    if len(bars) < 5:
        return {}
    closes = [b.close for b in bars]
    price = float(entry) if entry is not None else float(closes[-1])
    ema20 = compute_ema(closes, 20) if len(closes) >= 5 else None
    ema50 = compute_ema(closes, 50) if len(closes) >= 10 else None
    ema200 = compute_ema(closes, 200) if len(closes) >= 30 else None
    # Prefer None over 0.0 fake when insufficient history
    if len(closes) < 20:
        ema20 = compute_ema(closes, min(20, len(closes))) if closes else None
    atr = compute_atr(bars, 14) if len(bars) >= 15 else None
    if atr is not None and atr <= 0:
        atr = None
    vwap = compute_vwap(bars[-min(78, len(bars)):]) if bars else None
    rsi = compute_rsi(closes, 14)
    macd, macd_sig, macd_hist = compute_macd(closes)
    bb_mid, bb_up, bb_lo, bb_pct = compute_bollinger(closes)
    adx = compute_adx(bars, 14)
    stoch_k, stoch_d = compute_stochastic(bars)
    slope = compute_slope(closes, 20)
    trend = compute_trend_score(
        ema20=ema20, ema50=ema50, ema200=ema200, adx=adx, slope=slope,
    )
    vol = sum(b.volume for b in bars[-20:]) / min(20, len(bars)) if bars else None

    def dist(level: float | None) -> float | None:
        if level is None or abs(float(level)) < 1e-12:
            return None
        return round(pct_distance(price, float(level)), 6)

    return {
        "rsi": rsi,
        "ema20": None if ema20 is None else round(float(ema20), 8),
        "ema50": None if ema50 is None else round(float(ema50), 8),
        "ema200": None if ema200 is None else round(float(ema200), 8),
        "ema20_distance": dist(ema20),
        "ema50_distance": dist(ema50),
        "ema200_distance": dist(ema200),
        "vwap": None if vwap is None else round(float(vwap), 8),
        "vwap_distance": dist(vwap),
        "atr": None if atr is None else round(float(atr), 8),
        "atr_pct": atr_pct(atr, price),
        "macd": macd,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "bb_mid": bb_mid,
        "bb_upper": bb_up,
        "bb_lower": bb_lo,
        "bb_pct_b": bb_pct,
        "adx": adx,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "slope": slope,
        "trend": trend,
        "ema_trend": trend,
        "volume_ma20": None if vol is None else round(float(vol), 4),
        "close": price,
        "candle_n": float(len(bars)),
    }


__all__ = [
    "atr_pct",
    "compute_adx",
    "compute_bollinger",
    "compute_ema_series",
    "compute_macd",
    "compute_rsi",
    "compute_slope",
    "compute_stochastic",
    "compute_trend_score",
    "indicators_from_bars",
]
