"""Candle bar utilities for F.0 multitimeframe / exhaustion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class CandleBar:
    open_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def bars_from_rows(rows: Sequence[Any]) -> list[CandleBar]:
    out: list[CandleBar] = []
    for r in rows:
        out.append(CandleBar(
            open_ts=int(r["open_ts"]),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"] or 0),
        ))
    return out


def load_recent_candles(
    conn: Any,
    *,
    symbol: str,
    venue: str = "binance_futures",
    timeframe: str = "5m",
    limit: int = 120,
) -> list[CandleBar]:
    rows = conn.execute(
        """
        SELECT open_ts, open, high, low, close, volume
        FROM market_events_historical_candles
        WHERE venue = ? AND symbol = ? AND timeframe = ?
        ORDER BY open_ts DESC LIMIT ?
        """,
        (venue, symbol, timeframe, limit),
    ).fetchall()
    bars = bars_from_rows(reversed(rows))
    return bars


def aggregate_bars(bars: list[CandleBar], *, window_minutes: int, bar_minutes: int = 5) -> list[CandleBar]:
    """Roll up 5m bars into wider windows."""
    n = max(1, window_minutes // bar_minutes)
    if n <= 1:
        return bars
    out: list[CandleBar] = []
    for i in range(0, len(bars), n):
        chunk = bars[i: i + n]
        if len(chunk) < n:
            break
        out.append(CandleBar(
            open_ts=chunk[0].open_ts,
            open=chunk[0].open,
            high=max(b.high for b in chunk),
            low=min(b.low for b in chunk),
            close=chunk[-1].close,
            volume=sum(b.volume for b in chunk),
        ))
    return out


def compute_atr(bars: list[CandleBar], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-period:]
    return sum(window) / len(window) if window else 0.0


def compute_vwap(bars: list[CandleBar]) -> float:
    num = den = 0.0
    for b in bars:
        tp = (b.high + b.low + b.close) / 3.0
        num += tp * b.volume
        den += b.volume
    if den <= 0:
        return bars[-1].close if bars else 0.0
    return num / den


def compute_ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2.0 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def pct_distance(price: float, ref: float) -> float:
    if ref == 0:
        return 0.0
    return (price / ref - 1.0) * 100.0
