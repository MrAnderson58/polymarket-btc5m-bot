"""Phase G.1 — multi-timeframe trend windows and consecutive candle patterns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    load_recent_candles,
)

G1_WINDOWS_MINUTES = (5, 15, 30, 60, 120, 240)


@dataclass(frozen=True)
class WindowTrendG1:
    window_minutes: int
    cumulative_return_pct: float
    candle_count: int
    green_pct: float
    red_pct: float
    max_streak: int
    streak_direction: str  # UP / DOWN
    dominant_direction: str


@dataclass(frozen=True)
class ConsecutivePatternG1:
    window_minutes: int
    segments: list[dict[str, Any]]  # [{color, count}, ...]
    max_streak: int
    max_streak_color: str
    description: str


def _is_green(bar: CandleBar) -> bool:
    return bar.close >= bar.open


def _streak_at_end(bars: list[CandleBar]) -> tuple[int, str]:
    if not bars:
        return 0, "NEUTRAL"
    green = _is_green(bars[-1])
    color = "green" if green else "red"
    streak = 1
    for b in reversed(bars[:-1]):
        if _is_green(b) == green:
            streak += 1
        else:
            break
    direction = "UP" if green else "DOWN"
    return streak, direction


def _max_streak(bars: list[CandleBar]) -> tuple[int, str]:
    if not bars:
        return 0, "NEUTRAL"
    best = 1
    best_color = "green" if _is_green(bars[0]) else "red"
    cur = 1
    cur_color = best_color
    for b in bars[1:]:
        c = "green" if _is_green(b) else "red"
        if c == cur_color:
            cur += 1
        else:
            if cur > best:
                best = cur
                best_color = cur_color
            cur = 1
            cur_color = c
    if cur > best:
        best = cur
        best_color = cur_color
    direction = "UP" if best_color == "green" else "DOWN"
    return best, direction


def analyze_window_trend(
    bars: list[CandleBar],
    *,
    window_minutes: int,
) -> WindowTrendG1 | None:
    n = max(1, window_minutes // 5)
    if len(bars) < n:
        return None
    window_bars = bars[-n:]
    if not window_bars or window_bars[0].open <= 0:
        return None

    cumulative = (window_bars[-1].close / window_bars[0].open - 1.0) * 100.0
    greens = sum(1 for b in window_bars if _is_green(b))
    reds = len(window_bars) - greens
    total = len(window_bars)
    streak, streak_dir = _streak_at_end(window_bars)
    max_str, max_dir = _max_streak(window_bars)
    dominant = "UP" if cumulative >= 0 else "DOWN"

    return WindowTrendG1(
        window_minutes=window_minutes,
        cumulative_return_pct=round(cumulative, 3),
        candle_count=total,
        green_pct=round(greens / total * 100, 1) if total else 0.0,
        red_pct=round(reds / total * 100, 1) if total else 0.0,
        max_streak=max(max_str, streak),
        streak_direction=streak_dir,
        dominant_direction=dominant,
    )


def _run_length_segments(bars: list[CandleBar]) -> list[dict[str, Any]]:
    if not bars:
        return []
    segments: list[dict[str, Any]] = []
    cur_color = "green" if _is_green(bars[0]) else "red"
    cur_count = 1
    for b in bars[1:]:
        c = "green" if _is_green(b) else "red"
        if c == cur_color:
            cur_count += 1
        else:
            segments.append({"color": cur_color, "count": cur_count})
            cur_color = c
            cur_count = 1
    segments.append({"color": cur_color, "count": cur_count})
    return segments


def analyze_consecutive_pattern(
    bars: list[CandleBar],
    *,
    window_minutes: int,
) -> ConsecutivePatternG1 | None:
    n = max(1, window_minutes // 5)
    if len(bars) < n:
        return None
    window_bars = bars[-n:]
    segments = _run_length_segments(window_bars)
    if not segments:
        return None

    max_seg = max(segments, key=lambda s: s["count"])
    parts = []
    for s in segments:
        label = "зелёных" if s["color"] == "green" else "красных"
        parts.append(f"{s['count']} {label}")
    description = " → ".join(parts)

    return ConsecutivePatternG1(
        window_minutes=window_minutes,
        segments=segments,
        max_streak=int(max_seg["count"]),
        max_streak_color=str(max_seg["color"]),
        description=description,
    )


def analyze_all_windows(
    conn: Any,
    *,
    symbol: str,
    venue: str = "binance_futures",
) -> tuple[list[WindowTrendG1], list[ConsecutivePatternG1]]:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=300)
    if not bars:
        return [], []

    windows: list[WindowTrendG1] = []
    patterns: list[ConsecutivePatternG1] = []
    for wm in G1_WINDOWS_MINUTES:
        wt = analyze_window_trend(bars, window_minutes=wm)
        if wt:
            windows.append(wt)
        cp = analyze_consecutive_pattern(bars, window_minutes=wm)
        if cp:
            patterns.append(cp)
    return windows, patterns
