"""Phase F.2 Task D — deterministic market structure labels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import CandleBar, load_recent_candles

LABEL_HH = "Higher High"
LABEL_LL = "Lower Low"
LABEL_BOS = "Break of Structure"
LABEL_CHOCH = "Change of Character"
LABEL_SWEEP = "Liquidity Sweep"
LABEL_CONT = "Trend continuation"


@dataclass(frozen=True)
class MarketStructureResult:
    labels: list[str]
    detail: dict[str, Any]


def _swing_points(bars: list[CandleBar], lookback: int = 2) -> list[tuple[int, str, float]]:
    points: list[tuple[int, str, float]] = []
    for i in range(lookback, len(bars) - lookback):
        h = bars[i].high
        l = bars[i].low
        if all(h >= bars[j].high for j in range(i - lookback, i + lookback + 1) if j != i):
            points.append((i, "high", h))
        if all(l <= bars[j].low for j in range(i - lookback, i + lookback + 1) if j != i):
            points.append((i, "low", l))
    return sorted(points, key=lambda x: x[0])


def analyze_market_structure(
    conn: Any,
    *,
    symbol: str,
    shock_return_pct: float,
    venue: str = "binance_futures",
) -> MarketStructureResult:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=80)
    labels: list[str] = []
    detail: dict[str, Any] = {}

    if len(bars) < 15:
        return MarketStructureResult(labels=["insufficient_data"], detail=detail)

    swings = _swing_points(bars)
    highs = [p for p in swings if p[1] == "high"]
    lows = [p for p in swings if p[1] == "low"]

    if len(highs) >= 2:
        if highs[-1][2] > highs[-2][2]:
            labels.append(LABEL_HH)
        elif highs[-1][2] < highs[-2][2]:
            detail["lower_high"] = True

    if len(lows) >= 2:
        if lows[-1][2] < lows[-2][2]:
            labels.append(LABEL_LL)
        elif lows[-1][2] > lows[-2][2]:
            detail["higher_low"] = True

    last = bars[-1]
    if highs:
        prior_high = highs[-2][2] if len(highs) >= 2 else highs[-1][2]
        if last.high > prior_high and last.close < prior_high:
            labels.append(LABEL_SWEEP)
        elif last.close > prior_high and shock_return_pct > 0:
            labels.append(LABEL_BOS)
        elif last.close < prior_high and shock_return_pct < 0 and len(lows) >= 2:
            labels.append(LABEL_CHOCH)

    if len(highs) >= 3 and len(lows) >= 3:
        up_trend = highs[-1][2] > highs[-3][2] and lows[-1][2] > lows[-3][2]
        down_trend = highs[-1][2] < highs[-3][2] and lows[-1][2] < lows[-3][2]
        if (up_trend and shock_return_pct > 0) or (down_trend and shock_return_pct < 0):
            labels.append(LABEL_CONT)

    if not labels:
        labels.append("range")

    detail["swing_highs"] = len(highs)
    detail["swing_lows"] = len(lows)
    return MarketStructureResult(labels=labels, detail=detail)
