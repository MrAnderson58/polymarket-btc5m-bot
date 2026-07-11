"""Phase F.2 Task E — ATR percentile, expansion, exhaustion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import compute_atr, load_recent_candles


@dataclass(frozen=True)
class AtrExpansionResult:
    atr_percentile: float
    atr_expansion: float
    atr_exhaustion: bool
    current_atr: float


def _rolling_atrs(bars: list, period: int = 14) -> list[float]:
    out: list[float] = []
    for i in range(period + 1, len(bars) + 1):
        out.append(compute_atr(bars[:i], period))
    return out


def analyze_atr_expansion(
    conn: Any,
    *,
    symbol: str,
    venue: str = "binance_futures",
) -> AtrExpansionResult:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=120)
    if len(bars) < 20:
        return AtrExpansionResult(
            atr_percentile=50.0, atr_expansion=1.0, atr_exhaustion=False, current_atr=0.0,
        )

    atrs = _rolling_atrs(bars)
    current = atrs[-1]
    history = atrs[-min(100, len(atrs)):]
    sorted_h = sorted(history)
    rank = sum(1 for a in sorted_h if a <= current)
    percentile = round(100.0 * rank / len(sorted_h), 1)

    median = sorted_h[len(sorted_h) // 2] or 1e-9
    expansion = round(current / median, 2)

    exhaustion = False
    if len(atrs) >= 4:
        peak = max(atrs[-4:-1])
        if peak > median * 1.3 and current < peak * 0.85:
            exhaustion = True

    return AtrExpansionResult(
        atr_percentile=percentile,
        atr_expansion=expansion,
        atr_exhaustion=exhaustion,
        current_atr=round(current, 6),
    )
