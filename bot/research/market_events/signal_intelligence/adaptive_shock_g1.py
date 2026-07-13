"""Phase G.1 — ATR-adaptive shock thresholds per symbol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import (
    compute_atr,
    load_recent_candles,
)

# Base ATR-multiple gates; effective % = atr_pct * multiplier
G1_ADAPTIVE_MULTIPLIERS: dict[str, float] = {
    "BTC": 0.55,
    "ETH": 0.65,
    "SOL": 0.85,
    "DOGE": 1.10,
    "SUI": 0.90,
    "DEFAULT": 0.75,
}

G1_WINDOW_MINUTES = 30


@dataclass(frozen=True)
class AdaptiveThresholdG1:
    symbol: str
    window_minutes: int
    atr_pct: float
    threshold_pct: float
    multiplier: float
    description: str


def _symbol_multiplier(symbol: str) -> float:
    base = symbol.upper().replace("USDT", "").replace("/", "")
    return G1_ADAPTIVE_MULTIPLIERS.get(base, G1_ADAPTIVE_MULTIPLIERS["DEFAULT"])


def compute_adaptive_threshold(
    conn: Any,
    *,
    symbol: str,
    window_minutes: int = G1_WINDOW_MINUTES,
    venue: str = "binance_futures",
) -> AdaptiveThresholdG1:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=60)
    if not bars or bars[-1].close <= 0:
        mult = _symbol_multiplier(symbol)
        return AdaptiveThresholdG1(
            symbol=symbol,
            window_minutes=window_minutes,
            atr_pct=1.0,
            threshold_pct=round(mult * 1.0, 2),
            multiplier=mult,
            description=f"fallback threshold {mult:.1f}%",
        )

    atr = compute_atr(bars, period=14)
    price = bars[-1].close
    atr_pct = (atr / price) * 100.0 if price > 0 else 1.0
    mult = _symbol_multiplier(symbol)
    threshold = round(atr_pct * mult, 2)
    threshold = max(0.5, min(threshold, 6.0))

    # Human examples: BTC ~0.8%, SOL ~2.8%, DOGE ~4%
    desc = f"{threshold:.1f}% за {window_minutes}m (ATR {atr_pct:.2f}% × {mult:.2f})"
    return AdaptiveThresholdG1(
        symbol=symbol,
        window_minutes=window_minutes,
        atr_pct=round(atr_pct, 3),
        threshold_pct=threshold,
        multiplier=mult,
        description=desc,
    )


def move_exceeds_adaptive_threshold(
    conn: Any,
    *,
    symbol: str,
    return_pct: float,
    window_minutes: int = G1_WINDOW_MINUTES,
) -> bool:
    thr = compute_adaptive_threshold(conn, symbol=symbol, window_minutes=window_minutes)
    return abs(return_pct) >= thr.threshold_pct
