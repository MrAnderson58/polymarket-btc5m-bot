"""Phase F.4 — Trend Shock v2 with extended windows and lifecycle stages."""

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

STAGE_FORMING = "Trend Forming"
STAGE_ACCELERATION = "Trend Acceleration"
STAGE_SHOCK = "Trend Shock"
STAGE_CAPITULATION = "Capitulation"
STAGE_REVERSAL = "Reversal Candidate"

TREND_V2_WINDOWS: dict[int, dict[str, float]] = {
    10: {"min_cumulative_pct": 1.2, "min_accumulated_pct": 1.5, "min_consecutive": 2},
    15: {"min_cumulative_pct": 1.8, "min_accumulated_pct": 2.2, "min_consecutive": 3},
    30: {"min_cumulative_pct": 2.5, "min_accumulated_pct": 3.0, "min_consecutive": 3},
    60: {"min_cumulative_pct": 3.5, "min_accumulated_pct": 4.0, "min_consecutive": 4},
    120: {"min_cumulative_pct": 4.5, "min_accumulated_pct": 5.5, "min_consecutive": 5},
}


@dataclass(frozen=True)
class TrendShockV2Hit:
    window_minutes: int
    cumulative_return_pct: float
    accumulated_move_pct: float
    consecutive_bars: int
    acceleration_ratio: float
    atr_multiple: float
    volume_multiple: float
    funding: float | None
    open_interest_delta: float | None
    liquidations_score: float | None
    trend_score: float
    stage: str
    direction: str


@dataclass(frozen=True)
class TrendShockV2Signal:
    symbol: str
    event_ts: int
    primary: TrendShockV2Hit
    hits: list[TrendShockV2Hit]
    continuation_probability: float


def _dedup_key(symbol: str, event_ts: int, stage: str) -> str:
    bucket = event_ts // 300
    return hashlib.sha256(f"trend_v2:{symbol}:{stage}:{bucket}".encode()).hexdigest()[:32]


def _consecutive_streak(bars: list[CandleBar]) -> tuple[int, bool]:
    if len(bars) < 2:
        return 0, True
    last_up = bars[-1].close >= bars[-1].open
    streak = 1
    for b in reversed(bars[:-1]):
        if (b.close >= b.open) == last_up:
            streak += 1
        else:
            break
    return streak, last_up


def _acceleration(bars: list[CandleBar]) -> float:
    if len(bars) < 4:
        return 1.0
    mid = len(bars) // 2
    first = bars[:mid]
    second = bars[mid:]
    def _move(chunk: list[CandleBar]) -> float:
        if not chunk or chunk[0].open <= 0:
            return 0.0
        return abs((chunk[-1].close / chunk[0].open - 1.0) * 100.0)
    m1, m2 = _move(first), _move(second)
    if m1 <= 0.01:
        return m2 / 0.01
    return m2 / m1


def _classify_stage(
    *,
    abs_cumulative: float,
    acceleration: float,
    streak: int,
    vol_mult: float,
    exhaustion: bool,
) -> str:
    if exhaustion and abs_cumulative >= 2.5:
        return STAGE_REVERSAL
    if abs_cumulative >= 5.0 and vol_mult >= 3.0:
        return STAGE_CAPITULATION
    if abs_cumulative >= 2.5 and acceleration >= 1.4:
        return STAGE_ACCELERATION
    if abs_cumulative >= 2.0:
        return STAGE_SHOCK
    return STAGE_FORMING


def _continuation_prob(stage: str, abs_cumulative: float, acceleration: float) -> float:
    base = {
        STAGE_FORMING: 0.55,
        STAGE_ACCELERATION: 0.72,
        STAGE_SHOCK: 0.68,
        STAGE_CAPITULATION: 0.45,
        STAGE_REVERSAL: 0.35,
    }.get(stage, 0.5)
    adj = min(0.2, abs_cumulative / 30.0) + min(0.1, max(0, acceleration - 1) * 0.05)
    if stage == STAGE_REVERSAL:
        adj = -0.15
    return round(min(0.95, max(0.05, base + adj)), 2)


def _market_metrics(conn: Any, event_id: int | None) -> tuple[float | None, float | None, float | None]:
    funding = oi_delta = liq = None
    if event_id:
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
        exch = conn.execute(
            "SELECT funding, open_interest FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        if exch and funding is None and exch["funding"] is not None:
            funding = float(exch["funding"])
    return funding, oi_delta, liq


def detect_window_v2(
    bars: list[CandleBar],
    *,
    window_minutes: int,
    cfg: dict[str, float],
    funding: float | None = None,
    oi_delta: float | None = None,
    exhaustion: bool = False,
) -> TrendShockV2Hit | None:
    rolled = aggregate_bars(bars, window_minutes=window_minutes, bar_minutes=5)
    if len(rolled) < 2:
        return None

    n = max(2, window_minutes // 5)
    window_bars = rolled[-n:]
    first, last = window_bars[0], window_bars[-1]
    if first.open <= 0:
        return None

    cumulative = (last.close / first.open - 1.0) * 100.0
    accumulated = sum(abs((b.close / b.open - 1.0) * 100.0) for b in window_bars if b.open > 0)
    streak, _ = _consecutive_streak(window_bars)
    accel = _acceleration(window_bars)

    if abs(cumulative) < cfg["min_cumulative_pct"]:
        return None
    if accumulated < cfg["min_accumulated_pct"]:
        return None
    if streak < cfg["min_consecutive"]:
        return None

    atr = compute_atr(rolled, period=min(14, len(rolled) - 1)) or 1e-9
    atr_mult = abs(last.close - first.open) / atr
    vols = [b.volume for b in rolled[-21:-1] if b.volume > 0]
    avg_vol = sum(vols) / len(vols) if vols else last.volume or 1.0
    vol_mult = last.volume / avg_vol if avg_vol > 0 else 1.0

    stage = _classify_stage(
        abs_cumulative=abs(cumulative),
        acceleration=accel,
        streak=streak,
        vol_mult=vol_mult,
        exhaustion=exhaustion,
    )
    direction = "UP" if cumulative > 0 else "DOWN"
    score = round(
        min(100.0, abs(cumulative) * 7 + accumulated * 2.5 + streak * 3 + accel * 8 + vol_mult * 4),
        1,
    )

    return TrendShockV2Hit(
        window_minutes=window_minutes,
        cumulative_return_pct=round(cumulative, 3),
        accumulated_move_pct=round(accumulated, 3),
        consecutive_bars=streak,
        acceleration_ratio=round(accel, 3),
        atr_multiple=round(atr_mult, 3),
        volume_multiple=round(vol_mult, 3),
        funding=funding,
        open_interest_delta=oi_delta,
        liquidations_score=None,
        trend_score=score,
        stage=stage,
        direction=direction,
    )


def detect_trend_shock_v2(
    conn: Any,
    bars: list[CandleBar],
    *,
    symbol: str,
    event_id: int | None = None,
) -> TrendShockV2Signal | None:
    if len(bars) < 30:
        return None

    exhaustion = False
    if event_id:
        exh = conn.execute(
            "SELECT exhaustion_score FROM market_events_exhaustion WHERE source_event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        if exh and float(exh["exhaustion_score"] or 0) >= 60:
            exhaustion = True

    funding, oi_delta, liq = _market_metrics(conn, event_id)
    hits: list[TrendShockV2Hit] = []
    for window, cfg in TREND_V2_WINDOWS.items():
        hit = detect_window_v2(
            bars,
            window_minutes=window,
            cfg=cfg,
            funding=funding,
            oi_delta=oi_delta,
            exhaustion=exhaustion,
        )
        if hit:
            hits.append(hit)

    if not hits:
        return None

    primary = max(hits, key=lambda h: (abs(h.cumulative_return_pct), h.trend_score))
    cont = _continuation_prob(primary.stage, abs(primary.cumulative_return_pct), primary.acceleration_ratio)
    return TrendShockV2Signal(
        symbol=symbol,
        event_ts=bars[-1].open_ts,
        primary=primary,
        hits=hits,
        continuation_probability=cont,
    )


def persist_trend_shock_v2(
    conn: Any,
    signal: TrendShockV2Signal,
    *,
    source_event_id: int | None = None,
) -> int | None:
    p = signal.primary
    dk = _dedup_key(signal.symbol, signal.event_ts, p.stage)
    if conn.execute(
        "SELECT id FROM market_events_trend_shock_v2 WHERE dedup_key = ?",
        (dk,),
    ).fetchone():
        return None

    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_trend_shock_v2 (
          event_id, symbol, event_ts, stage, window_minutes, direction,
          cumulative_return_pct, accumulated_move_pct, consecutive_bars,
          acceleration_ratio, atr_multiple, volume_multiple,
          funding, open_interest_delta, liquidations_score,
          trend_score, continuation_probability, detectors_json,
          source_event_id, dedup_key, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_event_id, signal.symbol, signal.event_ts, p.stage, p.window_minutes,
            p.direction, p.cumulative_return_pct, p.accumulated_move_pct, p.consecutive_bars,
            p.acceleration_ratio, p.atr_multiple, p.volume_multiple,
            p.funding, p.open_interest_delta, p.liquidations_score,
            p.trend_score, signal.continuation_probability,
            json.dumps([
                {
                    "window_minutes": h.window_minutes,
                    "stage": h.stage,
                    "cumulative_return_pct": h.cumulative_return_pct,
                    "trend_score": h.trend_score,
                }
                for h in signal.hits
            ]),
            source_event_id, dk, int(time.time()),
        ),
    )


def scan_trend_shock_v2(
    conn: Any,
    *,
    symbol: str,
    source_event_id: int | None = None,
    venue: str = "binance_futures",
) -> TrendShockV2Signal | None:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=150)
    if not bars:
        return None
    sig = detect_trend_shock_v2(conn, bars, symbol=symbol, event_id=source_event_id)
    if sig:
        persist_trend_shock_v2(conn, sig, source_event_id=source_event_id)
    return sig


def load_trend_shock_v2(conn: Any, event_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM market_events_trend_shock_v2 WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    return dict(row) if row else None
