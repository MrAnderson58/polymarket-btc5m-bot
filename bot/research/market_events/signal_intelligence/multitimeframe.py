"""Independent multitimeframe shock detectors SHOCK_M5/M10/M15/M30."""

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
from bot.research.market_events.signal_intelligence.config import MTF_DETECTORS


@dataclass(frozen=True)
class MtfSignal:
    detector_id: str
    symbol: str
    event_ts: int
    window_minutes: int
    return_pct: float
    atr_multiple: float
    volume_multiple: float
    direction: str


def _dedup_key(detector_id: str, symbol: str, event_ts: int, direction: str) -> str:
    bucket = event_ts // 300
    raw = f"mtf:{detector_id}:{symbol}:{direction}:{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def detect_on_bars(
    bars: list[CandleBar],
    *,
    detector_id: str,
    symbol: str,
    cfg: dict[str, Any],
) -> MtfSignal | None:
    window = int(cfg["window_minutes"])
    min_ret = float(cfg["min_return_pct"])
    min_atr = float(cfg["min_atr_multiple"])
    if len(bars) < window + 14:
        return None

    rolled = aggregate_bars(bars, window_minutes=window, bar_minutes=5)
    if len(rolled) < 2:
        return None

    last = rolled[-1]
    prev = rolled[-2]
    if prev.close <= 0:
        return None
    ret = (last.close / prev.close - 1.0) * 100.0
    if abs(ret) < min_ret:
        return None

    atr = compute_atr(rolled, period=14)
    move = abs(last.close - prev.close)
    atr_mult = move / atr if atr > 0 else 0.0
    if atr_mult < min_atr:
        return None

    vols = [b.volume for b in rolled[-20:-1] if b.volume > 0]
    avg_vol = sum(vols) / len(vols) if vols else last.volume or 1.0
    vol_mult = last.volume / avg_vol if avg_vol > 0 else 1.0

    direction = "UP" if ret > 0 else "DOWN"
    return MtfSignal(
        detector_id=detector_id,
        symbol=symbol,
        event_ts=last.open_ts,
        window_minutes=window,
        return_pct=round(ret, 4),
        atr_multiple=round(atr_mult, 3),
        volume_multiple=round(vol_mult, 3),
        direction=direction,
    )


def persist_mtf_signal(
    conn: Any,
    signal: MtfSignal,
    *,
    source_event_id: int | None = None,
) -> int | None:
    dk = _dedup_key(signal.detector_id, signal.symbol, signal.event_ts, signal.direction)
    existing = conn.execute(
        "SELECT id FROM market_events_multitimeframe WHERE dedup_key = ?",
        (dk,),
    ).fetchone()
    if existing:
        return int(existing["id"])
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_multitimeframe (
          detector_id, symbol, event_ts, window_minutes, return_pct,
          atr_multiple, volume_multiple, direction, source_event_id,
          dedup_key, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.detector_id, signal.symbol, signal.event_ts, signal.window_minutes,
            signal.return_pct, signal.atr_multiple, signal.volume_multiple,
            signal.direction, source_event_id, dk, int(time.time()),
        ),
    )


def scan_multitimeframe(
    conn: Any,
    *,
    symbol: str,
    source_event_id: int | None = None,
    venue: str = "binance_futures",
) -> list[MtfSignal]:
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=120)
    if not bars:
        return []
    found: list[MtfSignal] = []
    for detector_id, cfg in MTF_DETECTORS.items():
        sig = detect_on_bars(bars, detector_id=detector_id, symbol=symbol, cfg=cfg)
        if sig:
            persist_mtf_signal(conn, sig, source_event_id=source_event_id)
            found.append(sig)
    return found


def multitimeframe_report(conn: Any, *, days: int = 7) -> str:
    since = int(time.time()) - days * 86400
    lines = ["MULTITIMEFRAME REPORT (F.0)", ""]
    for det in MTF_DETECTORS:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, AVG(return_pct) AS avg_ret
            FROM market_events_multitimeframe
            WHERE detector_id = ? AND event_ts >= ?
            """,
            (det, since),
        ).fetchone()
        n = int(row["n"] if row else 0)
        avg = row["avg_ret"] if row and row["avg_ret"] is not None else 0.0
        lines.append(f"  {det}: signals={n} avg_return={avg:.2f}%")
    return "\n".join(lines)
