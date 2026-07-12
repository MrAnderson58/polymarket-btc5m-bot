"""Phase F.3 Trend Shock — cumulative impulse detector (5/10/15/30m)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    aggregate_bars,
    compute_atr,
    load_recent_candles,
)

TREND_SHOCK_UP = "TREND_SHOCK_UP"
TREND_SHOCK_DOWN = "TREND_SHOCK_DOWN"

TREND_WINDOWS: dict[int, dict[str, float]] = {
    5: {"min_cumulative_pct": 1.0, "min_accumulated_pct": 1.2, "min_consecutive": 2},
    10: {"min_cumulative_pct": 1.5, "min_accumulated_pct": 1.8, "min_consecutive": 3},
    15: {"min_cumulative_pct": 2.0, "min_accumulated_pct": 2.5, "min_consecutive": 3},
    30: {"min_cumulative_pct": 2.5, "min_accumulated_pct": 3.0, "min_consecutive": 4},
}


@dataclass(frozen=True)
class TrendShockHit:
    window_minutes: int
    trend_class: str
    cumulative_return_pct: float
    accumulated_move_pct: float
    consecutive_bars: int
    atr_multiple: float
    volume_multiple: float
    trend_score: float


@dataclass(frozen=True)
class TrendShockSignal:
    symbol: str
    event_ts: int
    primary: TrendShockHit
    hits: list[TrendShockHit]


def _dedup_key(symbol: str, event_ts: int, trend_class: str) -> str:
    bucket = event_ts // 300
    raw = f"trend_shock:{symbol}:{trend_class}:{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _consecutive_same_direction(bars: list[CandleBar]) -> tuple[int, bool]:
    if len(bars) < 2:
        return 0, True
    last_up = bars[-1].close >= bars[-1].open
    streak = 1
    for b in reversed(bars[:-1]):
        up = b.close >= b.open
        if up == last_up:
            streak += 1
        else:
            break
    return streak, last_up


def _detect_window(
    bars: list[CandleBar],
    *,
    window_minutes: int,
    cfg: dict[str, float],
) -> TrendShockHit | None:
    rolled = aggregate_bars(bars, window_minutes=window_minutes, bar_minutes=5)
    if len(rolled) < 2:
        return None

    window_bars = rolled[-max(2, window_minutes // 5):]
    first, last = window_bars[0], window_bars[-1]
    if first.open <= 0:
        return None

    cumulative = (last.close / first.open - 1.0) * 100.0
    accumulated = sum(
        abs((b.close / b.open - 1.0) * 100.0) for b in window_bars if b.open > 0
    )
    streak, _ = _consecutive_same_direction(window_bars)

    if abs(cumulative) < cfg["min_cumulative_pct"]:
        return None
    if accumulated < cfg["min_accumulated_pct"]:
        return None
    if streak < cfg["min_consecutive"]:
        return None

    atr = compute_atr(rolled, period=min(14, len(rolled) - 1)) or 1e-9
    move = abs(last.close - first.open)
    atr_mult = move / atr

    vols = [b.volume for b in rolled[-21:-1] if b.volume > 0]
    avg_vol = sum(vols) / len(vols) if vols else last.volume or 1.0
    vol_mult = last.volume / avg_vol if avg_vol > 0 else 1.0

    trend_class = TREND_SHOCK_UP if cumulative > 0 else TREND_SHOCK_DOWN
    score = round(
        min(100.0, abs(cumulative) * 8 + accumulated * 3 + streak * 4 + atr_mult * 5),
        1,
    )

    return TrendShockHit(
        window_minutes=window_minutes,
        trend_class=trend_class,
        cumulative_return_pct=round(cumulative, 3),
        accumulated_move_pct=round(accumulated, 3),
        consecutive_bars=streak,
        atr_multiple=round(atr_mult, 3),
        volume_multiple=round(vol_mult, 3),
        trend_score=score,
    )


def detect_trend_shock(bars: list[CandleBar], *, symbol: str) -> TrendShockSignal | None:
    if len(bars) < 20:
        return None

    hits: list[TrendShockHit] = []
    for window, cfg in TREND_WINDOWS.items():
        hit = _detect_window(bars, window_minutes=window, cfg=cfg)
        if hit:
            hits.append(hit)

    if not hits:
        return None

    primary = max(hits, key=lambda h: abs(h.cumulative_return_pct))
    return TrendShockSignal(
        symbol=symbol,
        event_ts=bars[-1].open_ts,
        primary=primary,
        hits=hits,
    )


def persist_trend_shock(
    conn: Any,
    signal: TrendShockSignal,
    *,
    source_event_id: int | None = None,
) -> int | None:
    p = signal.primary
    dk = _dedup_key(signal.symbol, signal.event_ts, p.trend_class)
    if conn.execute(
        "SELECT id FROM market_events_trend_shock WHERE dedup_key = ?",
        (dk,),
    ).fetchone():
        return None

    detectors = [
        {
            "window_minutes": h.window_minutes,
            "trend_class": h.trend_class,
            "cumulative_return_pct": h.cumulative_return_pct,
            "accumulated_move_pct": h.accumulated_move_pct,
            "trend_score": h.trend_score,
        }
        for h in signal.hits
    ]

    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_trend_shock (
          event_id, symbol, event_ts, trend_class, window_minutes,
          cumulative_return_pct, accumulated_move_pct, consecutive_bars,
          atr_multiple, volume_multiple, trend_score, detectors_json,
          source_event_id, dedup_key, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_event_id, signal.symbol, signal.event_ts, p.trend_class,
            p.window_minutes, p.cumulative_return_pct, p.accumulated_move_pct,
            p.consecutive_bars, p.atr_multiple, p.volume_multiple, p.trend_score,
            json.dumps(detectors), source_event_id, dk, int(time.time()),
        ),
    )


def scan_trend_shock(
    conn: Any,
    *,
    symbol: str,
    source_event_id: int | None = None,
    venue: str = "binance_futures",
) -> TrendShockSignal | None:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=120)
    if not bars:
        return None
    sig = detect_trend_shock(bars, symbol=symbol)
    if sig:
        persist_trend_shock(conn, sig, source_event_id=source_event_id)
    return sig


def load_trend_shock(conn: Any, event_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM market_events_trend_shock WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    return dict(row) if row else None
