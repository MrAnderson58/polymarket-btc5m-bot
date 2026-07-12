"""Phase F.7 Task I — long trend shock windows (5m–240m)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    load_recent_candles,
)

STAGE_SLOW_BLEED = "Slow bleed"
STAGE_DISTRIBUTION = "Distribution"
STAGE_CAPITULATION = "Capitulation"
STAGE_PANIC = "Panic"
STAGE_RECOVERY = "Recovery"

LONG_TREND_WINDOWS: dict[int, dict[str, float]] = {
    5: {"min_cumulative_pct": 0.8, "min_bars": 2},
    10: {"min_cumulative_pct": 1.2, "min_bars": 3},
    15: {"min_cumulative_pct": 1.5, "min_bars": 3},
    30: {"min_cumulative_pct": 2.0, "min_bars": 4},
    60: {"min_cumulative_pct": 2.8, "min_bars": 5},
    120: {"min_cumulative_pct": 3.5, "min_bars": 6},
    240: {"min_cumulative_pct": 5.0, "min_bars": 8},
}


@dataclass(frozen=True)
class LongTrendShockF7:
    primary_stage: str
    primary_window: int
    cumulative_return_pct: float
    direction: str
    hits: list[dict[str, Any]]
    summary: str


def _classify_long_stage(
    *,
    cumulative: float,
    window_min: int,
    streak: int,
    vol_mult: float,
    recovering: bool,
) -> str:
    abs_c = abs(cumulative)
    if recovering and cumulative > -1.0:
        return STAGE_RECOVERY
    if abs_c >= 6.0 and vol_mult >= 2.5:
        return STAGE_PANIC
    if abs_c >= 4.0 and vol_mult >= 2.0:
        return STAGE_CAPITULATION
    if window_min >= 60 and abs_c >= 2.5 and streak >= 4:
        return STAGE_DISTRIBUTION
    if window_min >= 30 and abs_c >= 1.5:
        return STAGE_SLOW_BLEED
    if abs_c >= 3.0:
        return STAGE_CAPITULATION
    return STAGE_SLOW_BLEED


def _scan_window(bars: list[CandleBar], window_min: int) -> dict[str, Any] | None:
    n_bars = max(2, window_min // 5)
    if len(bars) < n_bars + 1:
        return None
    chunk = bars[-n_bars:]
    start, end = chunk[0].open, chunk[-1].close
    if start <= 0:
        return None
    cumulative = (end / start - 1.0) * 100.0
    cfg = LONG_TREND_WINDOWS[window_min]
    if abs(cumulative) < cfg["min_cumulative_pct"]:
        return None

    direction = "DOWN" if cumulative < 0 else "UP"
    streak = 1
    last_up = chunk[-1].close >= chunk[-1].open
    for b in reversed(chunk[:-1]):
        if (b.close >= b.open) == last_up:
            streak += 1
        else:
            break

    vols = [b.volume for b in chunk]
    avg_vol = sum(vols) / len(vols) if vols else 1.0
    baseline = [b.volume for b in bars[-n_bars * 3:-n_bars]] or [avg_vol]
    base_avg = sum(baseline) / len(baseline) if baseline else avg_vol
    vol_mult = avg_vol / base_avg if base_avg > 0 else 1.0

    recovering = cumulative < 0 and chunk[-1].close > chunk[-2].close and streak <= 2
    stage = _classify_long_stage(
        cumulative=cumulative,
        window_min=window_min,
        streak=streak,
        vol_mult=vol_mult,
        recovering=recovering,
    )
    return {
        "window_minutes": window_min,
        "cumulative_return_pct": round(cumulative, 2),
        "direction": direction,
        "streak": streak,
        "volume_multiple": round(vol_mult, 2),
        "stage": stage,
    }


def analyze_long_trend_shock(conn: Any, *, symbol: str) -> LongTrendShockF7 | None:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=60)
    if len(bars) < 10:
        return None

    hits: list[dict[str, Any]] = []
    for window_min in sorted(LONG_TREND_WINDOWS):
        hit = _scan_window(bars, window_min)
        if hit:
            hits.append(hit)

    if not hits:
        return None

    primary = max(hits, key=lambda h: abs(h["cumulative_return_pct"]))
    stage = primary["stage"]
    summaries = {
        STAGE_SLOW_BLEED: "Медленное истощение — давление нарастает постепенно",
        STAGE_DISTRIBUTION: "Distribution — распределение перед разворотом",
        STAGE_CAPITULATION: "Capitulation — капитуляция продавцов",
        STAGE_PANIC: "Panic — паническая распродажа",
        STAGE_RECOVERY: "Recovery — начало восстановления",
    }
    return LongTrendShockF7(
        primary_stage=stage,
        primary_window=int(primary["window_minutes"]),
        cumulative_return_pct=float(primary["cumulative_return_pct"]),
        direction=str(primary["direction"]),
        hits=hits,
        summary=summaries.get(stage, stage),
    )
