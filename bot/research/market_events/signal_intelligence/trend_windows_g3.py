"""Phase G.3 — multi-timeframe long-trend detector."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    aggregate_bars,
    load_recent_candles,
)
from bot.research.market_events.signal_intelligence.trend_windows_g1 import (
    analyze_consecutive_pattern,
    analyze_window_trend,
)

G3_WINDOWS_MINUTES = (5, 15, 30, 60, 120, 240)

PATTERN_SLOW_BLEED = "slow_bleed"
PATTERN_STAIR_STEP = "stair_step"
PATTERN_COMPRESSION = "compression"
PATTERN_DISTRIBUTION = "distribution"
PATTERN_ACCUMULATION = "accumulation"
PATTERN_CONSECUTIVE = "consecutive"


@dataclass(frozen=True)
class TrendWindowG3:
    symbol: str
    window_minutes: int
    pattern_type: str
    consecutive_candles: int
    trend_score: float
    direction: str
    details: dict[str, Any]


def _body_pct(bar: CandleBar) -> float:
    if bar.open <= 0:
        return 0.0
    return abs(bar.close / bar.open - 1.0) * 100.0


def _detect_pattern(
    bars: list[CandleBar],
    *,
    window_minutes: int,
) -> TrendWindowG3 | None:
    wt = analyze_window_trend(bars, window_minutes=window_minutes)
    if not wt:
        return None

    cp = analyze_consecutive_pattern(bars, window_minutes=window_minutes)
    streak = cp.max_streak if cp else wt.max_streak
    direction = wt.dominant_direction

    bodies = [_body_pct(b) for b in bars[-max(1, window_minutes // 5):]]
    avg_body = sum(bodies) / len(bodies) if bodies else 0.0
    range_pct = 0.0
    if bars and bars[0].open > 0:
        window_bars = bars[-max(1, window_minutes // 5):]
        hi = max(b.high for b in window_bars)
        lo = min(b.low for b in window_bars)
        range_pct = (hi / lo - 1.0) * 100.0 if lo > 0 else 0.0

    pattern = PATTERN_CONSECUTIVE
    score = min(100.0, streak * 4.0 + abs(wt.cumulative_return_pct) * 3.0)

    if streak >= 5 and avg_body < 0.35 and abs(wt.cumulative_return_pct) >= 1.0:
        pattern = PATTERN_SLOW_BLEED
        score = min(100.0, score + 15.0)
    elif cp and len(cp.segments) >= 3 and all(s["count"] >= 2 for s in cp.segments[-3:]):
        pattern = PATTERN_STAIR_STEP
        score = min(100.0, score + 10.0)
    elif range_pct < 1.2 and streak >= 4:
        pattern = PATTERN_COMPRESSION
        score = min(100.0, score + 12.0)
    elif direction == "DOWN" and wt.cumulative_return_pct < -1.5 and streak >= 6:
        pattern = PATTERN_DISTRIBUTION
        score = min(100.0, score + 8.0)
    elif direction == "UP" and wt.cumulative_return_pct > 1.0 and streak >= 5:
        pattern = PATTERN_ACCUMULATION
        score = min(100.0, score + 8.0)

    if streak < 5 and pattern == PATTERN_CONSECUTIVE:
        return None

    details = {
        "cumulative_return_pct": wt.cumulative_return_pct,
        "max_streak": streak,
        "avg_body_pct": round(avg_body, 3),
        "range_pct": round(range_pct, 3),
        "description": cp.description if cp else f"{streak} {direction.lower()} candles",
    }
    return TrendWindowG3(
        symbol="",
        window_minutes=window_minutes,
        pattern_type=pattern,
        consecutive_candles=streak,
        trend_score=round(min(100.0, max(0.0, score)), 1),
        direction=direction,
        details=details,
    )


def detect_trends_g3(
    conn: Any,
    *,
    symbol: str,
    venue: str = "binance_futures",
) -> list[TrendWindowG3]:
    bars_5m = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=300)
    if len(bars_5m) < 24:
        return []

    out: list[TrendWindowG3] = []
    for window in G3_WINDOWS_MINUTES:
        agg = aggregate_bars(bars_5m, window_minutes=window, bar_minutes=5)
        tw = _detect_pattern(agg if window > 5 else bars_5m, window_minutes=window)
        if tw:
            out.append(TrendWindowG3(
                symbol=symbol,
                window_minutes=tw.window_minutes,
                pattern_type=tw.pattern_type,
                consecutive_candles=tw.consecutive_candles,
                trend_score=tw.trend_score,
                direction=tw.direction,
                details=tw.details,
            ))
    return out


def persist_trends_g3(conn: Any, *, snapshot_id: int, trends: list[TrendWindowG3]) -> int:
    n = 0
    now = int(time.time())
    for t in trends:
        insert_returning_id(
            conn,
            """
            INSERT INTO market_trend_windows_g3 (
              snapshot_id, symbol, window_minutes, pattern_type, consecutive_candles,
              trend_score, direction, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                t.symbol,
                t.window_minutes,
                t.pattern_type,
                t.consecutive_candles,
                t.trend_score,
                t.direction,
                json.dumps(t.details),
                now,
            ),
        )
        n += 1
    return n


def run_trend_detection_g3(
    conn: Any,
    *,
    snapshot_id: int,
    symbols: tuple[str, ...] = ("SOL", "ETH", "BNB", "BTC"),
) -> list[TrendWindowG3]:
    all_trends: list[TrendWindowG3] = []
    for sym in symbols:
        trends = detect_trends_g3(conn, symbol=sym)
        all_trends.extend(trends)
    if all_trends:
        persist_trends_g3(conn, snapshot_id=snapshot_id, trends=all_trends)
    return all_trends
