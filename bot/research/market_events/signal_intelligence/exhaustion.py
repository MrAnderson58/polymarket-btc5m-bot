"""Trend exhaustion detector — research only, no paper trades."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    compute_atr,
    compute_ema,
    compute_vwap,
    load_recent_candles,
    pct_distance,
)
from bot.research.market_events.signal_intelligence.config import (
    EXHAUSTION_MIN_CONSECUTIVE,
    EXHAUSTION_MIN_SCORE,
)


@dataclass(frozen=True)
class ExhaustionSignal:
    symbol: str
    event_ts: int
    exhaustion_score: float
    reasons: list[str]
    consecutive_candles: int
    cumulative_return_pct: float
    atr_expansion: float
    volume_expansion: float
    vwap_distance_pct: float
    ema20_distance_pct: float
    ema50_distance_pct: float


def _dedup_key(symbol: str, event_ts: int) -> str:
    bucket = event_ts // 900
    return hashlib.sha256(f"exh:{symbol}:{bucket}".encode()).hexdigest()[:32]


def detect_exhaustion(bars: list[CandleBar], *, symbol: str) -> ExhaustionSignal | None:
    if len(bars) < 30:
        return None

    closes = [b.close for b in bars]
    direction_up = closes[-1] >= closes[-2]

    streak = 1
    for i in range(len(bars) - 2, 0, -1):
        up = bars[i].close >= bars[i].open
        if up == direction_up:
            streak += 1
        else:
            break
    if streak < EXHAUSTION_MIN_CONSECUTIVE:
        return None

    window = bars[-streak:]
    cum_ret = (window[-1].close / window[0].open - 1.0) * 100.0
    if direction_up and cum_ret <= 0:
        cum_ret = abs(cum_ret)
    if not direction_up and cum_ret >= 0:
        cum_ret = -abs(cum_ret)

    atr_recent = compute_atr(bars[-15:], period=14)
    atr_base = compute_atr(bars[-30:-15], period=14) or atr_recent or 1.0
    atr_exp = atr_recent / atr_base if atr_base > 0 else 1.0

    vol_recent = sum(b.volume for b in bars[-5:]) / 5.0
    vol_base = sum(b.volume for b in bars[-25:-5]) / 20.0 or vol_recent or 1.0
    vol_exp = vol_recent / vol_base if vol_base > 0 else 1.0

    vwap = compute_vwap(bars[-20:])
    ema20 = compute_ema(closes[-20:], 20)
    ema50 = compute_ema(closes[-50:], 50) if len(closes) >= 50 else compute_ema(closes, len(closes))
    price = closes[-1]
    vwap_dist = pct_distance(price, vwap)
    ema20_dist = pct_distance(price, ema20)
    ema50_dist = pct_distance(price, ema50)

    reasons: list[str] = []
    if streak >= EXHAUSTION_MIN_CONSECUTIVE:
        color = "green" if direction_up else "red"
        reasons.append(f"{streak} {color} candles")
    if vol_exp >= 2.0:
        reasons.append(f"volume {vol_exp:.1f}x")
    if atr_exp >= 1.8:
        reasons.append(f"ATR {atr_exp:.1f}x")
    if abs(ema20_dist) >= 4.0:
        reasons.append(f"EMA20 {ema20_dist:+.1f}%")
    if abs(vwap_dist) >= 4.0:
        reasons.append(f"VWAP {vwap_dist:+.1f}%")

    score = 0.0
    score += min(streak * 8, 32)
    score += min(max(vol_exp - 1, 0) * 12, 24)
    score += min(max(atr_exp - 1, 0) * 10, 20)
    score += min(abs(ema20_dist) * 1.5, 12)
    score += min(abs(vwap_dist) * 1.2, 12)
    score = round(min(100.0, max(0.0, score)), 1)

    if score < EXHAUSTION_MIN_SCORE:
        return None

    return ExhaustionSignal(
        symbol=symbol,
        event_ts=bars[-1].open_ts,
        exhaustion_score=score,
        reasons=reasons,
        consecutive_candles=streak,
        cumulative_return_pct=round(cum_ret, 3),
        atr_expansion=round(atr_exp, 3),
        volume_expansion=round(vol_exp, 3),
        vwap_distance_pct=round(vwap_dist, 3),
        ema20_distance_pct=round(ema20_dist, 3),
        ema50_distance_pct=round(ema50_dist, 3),
    )


def persist_exhaustion(
    conn: Any,
    signal: ExhaustionSignal,
    *,
    source_event_id: int | None = None,
) -> int | None:
    dk = _dedup_key(signal.symbol, signal.event_ts)
    if conn.execute(
        "SELECT id FROM market_events_exhaustion WHERE dedup_key = ?",
        (dk,),
    ).fetchone():
        return None
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_exhaustion (
          symbol, event_ts, event_type, exhaustion_score, reasons_json,
          consecutive_candles, cumulative_return_pct, atr_expansion,
          volume_expansion, vwap_distance_pct, ema20_distance_pct,
          ema50_distance_pct, source_event_id, dedup_key, created_at
        ) VALUES (?, ?, 'TREND_EXHAUSTION', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.symbol, signal.event_ts, signal.exhaustion_score,
            json.dumps(signal.reasons),
            signal.consecutive_candles, signal.cumulative_return_pct,
            signal.atr_expansion, signal.volume_expansion,
            signal.vwap_distance_pct, signal.ema20_distance_pct,
            signal.ema50_distance_pct, source_event_id, dk, int(time.time()),
        ),
    )


def scan_exhaustion(
    conn: Any,
    *,
    symbol: str,
    source_event_id: int | None = None,
    venue: str = "binance_futures",
) -> ExhaustionSignal | None:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=60)
    if not bars:
        return None
    sig = detect_exhaustion(bars, symbol=symbol)
    if sig:
        persist_exhaustion(conn, sig, source_event_id=source_event_id)
    return sig


def exhaustion_report(conn: Any, *, days: int = 7) -> str:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        """
        SELECT symbol, exhaustion_score, reasons_json, event_ts
        FROM market_events_exhaustion WHERE event_ts >= ?
        ORDER BY exhaustion_score DESC LIMIT 20
        """,
        (since,),
    ).fetchall()
    lines = ["EXHAUSTION REPORT (F.0)", ""]
    if not rows:
        lines.append("  (no signals)")
        return "\n".join(lines)
    for r in rows:
        reasons = json.loads(r["reasons_json"] or "[]")
        lines.append(
            f"  {r['symbol']} score={r['exhaustion_score']:.0f} "
            f"reasons={', '.join(reasons[:3])}",
        )
    return "\n".join(lines)
