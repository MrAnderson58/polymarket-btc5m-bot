"""Phase G.1 — slow trend shock, liquidity accumulation, capitulation, probabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    compute_atr,
)
from bot.research.market_events.signal_intelligence.trend_windows_g1 import (
    WindowTrendG1,
    analyze_window_trend,
)

SIGNAL_SLOW_TREND = "SLOW_TREND_SHOCK"
SIGNAL_LIQUIDITY_ACCUM = "LIQUIDITY_ACCUMULATION"
SIGNAL_CAPITULATION = "CAPITULATION"


@dataclass(frozen=True)
class SlowTrendShockG1:
    window_minutes: int
    cumulative_return_pct: float
    consecutive_same_color: int
    max_single_bar_pct: float
    direction: str
    description: str


@dataclass(frozen=True)
class LiquidityAccumulationG1:
    funding_negative: bool
    oi_rising: bool
    price_falling: bool
    volume_rising: bool
    score: float
    description: str


@dataclass(frozen=True)
class CapitulationG1:
    window_minutes: int
    volume_multiple: float
    atr_multiple: float
    funding_extreme: bool
    liquidation_intensity: float
    description: str


@dataclass(frozen=True)
class SweepProbabilityG1:
    continuation_probability: float
    reversal_probability: float
    primary_driver: str


def _max_single_bar_move(bars: list[CandleBar]) -> float:
    best = 0.0
    for b in bars:
        if b.open <= 0:
            continue
        best = max(best, abs((b.close / b.open - 1.0) * 100.0))
    return best


def detect_slow_trend_shock(
    bars: list[CandleBar],
    *,
    window_minutes: int = 120,
    min_cumulative_pct: float = 1.8,
    max_single_bar_pct: float = 1.2,
    min_consecutive: int = 8,
) -> SlowTrendShockG1 | None:
    """Slow directional move without one large candle."""
    wt = analyze_window_trend(bars, window_minutes=window_minutes)
    if not wt:
        return None

    n = max(1, window_minutes // 5)
    window_bars = bars[-n:] if len(bars) >= n else bars
    max_bar = _max_single_bar_move(window_bars)

    if abs(wt.cumulative_return_pct) < min_cumulative_pct:
        return None
    if max_bar > max_single_bar_pct:
        return None
    if wt.max_streak < min_consecutive:
        return None

    direction = wt.dominant_direction
    color = "красных" if direction == "DOWN" else "зелёных"
    desc = (
        f"{abs(wt.cumulative_return_pct):.1f}% за {window_minutes // 60}ч, "
        f"{wt.max_streak} {color} свечей, без большой свечи"
    )
    return SlowTrendShockG1(
        window_minutes=window_minutes,
        cumulative_return_pct=wt.cumulative_return_pct,
        consecutive_same_color=wt.max_streak,
        max_single_bar_pct=round(max_bar, 3),
        direction=direction,
        description=desc,
    )


def detect_liquidity_accumulation(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    price_return_pct: float,
    volume_multiple: float,
) -> LiquidityAccumulationG1 | None:
    funding = oi_delta = None
    row = conn.execute(
        """
        SELECT funding_delta, oi_delta FROM market_events_funding_oi_history_f2
        WHERE event_id = ? AND timeframe = '5m' LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if row:
        funding = float(row["funding_delta"]) if row["funding_delta"] is not None else None
        oi_delta = float(row["oi_delta"]) if row["oi_delta"] is not None else None
    if funding is None:
        exch = conn.execute(
            "SELECT funding FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        if exch and exch["funding"] is not None:
            funding = float(exch["funding"])

    funding_neg = funding is not None and funding < 0
    oi_rising = oi_delta is not None and oi_delta > 0
    price_falling = price_return_pct < -0.5
    volume_rising = volume_multiple >= 1.3

    hits = sum([funding_neg, oi_rising, price_falling, volume_rising])
    if hits < 3:
        return None

    score = round(hits / 4 * 100 + min(20, volume_multiple * 5), 1)
    parts = []
    if funding_neg:
        parts.append("Funding ↓")
    if oi_rising:
        parts.append("OI ↑")
    if price_falling:
        parts.append("Цена ↓")
    if volume_rising:
        parts.append(f"Объём {volume_multiple:.1f}×")

    return LiquidityAccumulationG1(
        funding_negative=funding_neg,
        oi_rising=oi_rising,
        price_falling=price_falling,
        volume_rising=volume_rising,
        score=score,
        description=" + ".join(parts),
    )


def detect_capitulation(
    bars: list[CandleBar],
    *,
    window_minutes: int = 15,
    min_volume_mult: float = 3.5,
    min_atr_mult: float = 2.0,
    funding: float | None = None,
    liquidation_intensity: float = 0.0,
) -> CapitulationG1 | None:
    n = max(1, window_minutes // 5)
    if len(bars) < n + 14:
        return None
    window_bars = bars[-n:]
    if not window_bars:
        return None

    vols = [b.volume for b in bars[-21:-n] if b.volume > 0]
    avg_vol = sum(vols) / len(vols) if vols else window_bars[-1].volume or 1.0
    vol_mult = window_bars[-1].volume / avg_vol if avg_vol > 0 else 1.0

    atr = compute_atr(bars, period=14) or 1e-9
    move = abs(window_bars[-1].close - window_bars[0].open)
    atr_mult = move / atr

    funding_extreme = funding is not None and abs(funding) > 0.0003
    liq_intense = liquidation_intensity >= 0.5

    if vol_mult < min_volume_mult and atr_mult < min_atr_mult:
        return None
    if vol_mult < min_volume_mult * 0.7 and not liq_intense:
        return None

    desc = f"{window_minutes}m: объём {vol_mult:.1f}×, ATR {atr_mult:.1f}×"
    if funding_extreme:
        desc += ", Funding экстремальный"
    if liq_intense:
        desc += ", ликвидации"

    return CapitulationG1(
        window_minutes=window_minutes,
        volume_multiple=round(vol_mult, 2),
        atr_multiple=round(atr_mult, 2),
        funding_extreme=funding_extreme,
        liquidation_intensity=round(liquidation_intensity, 3),
        description=desc,
    )


def compute_sweep_probability(
    *,
    slow_trend: SlowTrendShockG1 | None,
    liquidity: LiquidityAccumulationG1 | None,
    capitulation: CapitulationG1 | None,
    windows: list[WindowTrendG1],
    historical_reversal_rate: float = 0.5,
) -> SweepProbabilityG1:
    """Continuation vs reversal probability from G.1 factors."""
    reversal = historical_reversal_rate
    driver = "historical"

    if liquidity and liquidity.score >= 75:
        reversal = min(0.92, reversal + 0.25)
        driver = "liquidity_accumulation"
    elif capitulation:
        reversal = min(0.90, reversal + 0.20 + capitulation.volume_multiple * 0.02)
        driver = "capitulation"
    elif slow_trend:
        reversal = min(0.85, reversal + 0.15 + slow_trend.consecutive_same_color * 0.01)
        driver = "slow_trend"

    for w in windows:
        if w.window_minutes == 15 and w.max_streak >= 8 and abs(w.cumulative_return_pct) >= 2.0:
            reversal = min(0.88, reversal + 0.12)
            if driver == "historical":
                driver = "15m_streak"

    reversal = round(min(0.95, max(0.05, reversal)), 3)
    continuation = round(1.0 - reversal, 3)
    return SweepProbabilityG1(
        continuation_probability=continuation,
        reversal_probability=reversal,
        primary_driver=driver,
    )
