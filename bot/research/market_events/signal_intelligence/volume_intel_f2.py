"""Phase F.2 Task C — relative volume and VWAP deviation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import (
    compute_vwap,
    load_recent_candles,
    pct_distance,
)


@dataclass(frozen=True)
class VolumeIntelResult:
    rvol_20: float
    rvol_100: float
    vwap_deviation_pct: float
    volume_label: str


def _relative_volume(bars: list, window: int) -> float:
    if len(bars) < window + 1:
        return 1.0
    recent = bars[-1].volume
    avg = sum(b.volume for b in bars[-window - 1:-1]) / window
    if avg <= 0:
        return 1.0
    return recent / avg


def analyze_volume_intel(
    conn: Any,
    *,
    symbol: str,
    venue: str = "binance_futures",
) -> VolumeIntelResult:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=120)
    if not bars:
        return VolumeIntelResult(
            rvol_20=1.0, rvol_100=1.0, vwap_deviation_pct=0.0, volume_label="1.0× среднего",
        )

    rvol_20 = round(_relative_volume(bars, 20), 2)
    rvol_100 = round(_relative_volume(bars, min(100, len(bars) - 1)), 2)
    vwap = compute_vwap(bars[-20:])
    price = bars[-1].close
    vwap_dev = round(pct_distance(price, vwap), 2)
    peak = max(rvol_20, rvol_100)
    label = f"{peak:.1f}× среднего"
    return VolumeIntelResult(
        rvol_20=rvol_20,
        rvol_100=rvol_100,
        vwap_deviation_pct=vwap_dev,
        volume_label=label,
    )
