"""Phase G.5.1 — candle pattern analysis per window."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.candles import CandleBar, load_recent_candles
from bot.research.market_events.signal_intelligence.research_lake_types_g51 import G51_WINDOWS

_TABLE = "market_events_candle_patterns_g51"


def _candle_color(b: CandleBar) -> str:
    return "G" if b.close >= b.open else "R"


def _body_pct(b: CandleBar) -> float:
    if b.open <= 0:
        return 0.0
    return abs(b.close - b.open) / b.open * 100.0


def _wick_pct(b: CandleBar) -> float:
    body_top = max(b.open, b.close)
    body_bot = min(b.open, b.close)
    if b.open <= 0:
        return 0.0
    upper = (b.high - body_top) / b.open * 100.0
    lower = (body_bot - b.low) / b.open * 100.0
    return upper + lower


def _largest_candle_pct(bars: list[CandleBar]) -> float:
    if not bars:
        return 0.0
    return max(_body_pct(b) + _wick_pct(b) for b in bars)


def _window_bars(bars: list[CandleBar], *, end_ts: int, window_sec: int) -> list[CandleBar]:
    start = end_ts - window_sec
    return [b for b in bars if start <= b.open_ts <= end_ts]


def build_candle_patterns_g51(
    conn: Any,
    *,
    candidate_id: int,
    symbol: str,
    anchor_ts: int,
) -> list[dict[str, Any]]:
    bars = load_recent_candles(conn, symbol=symbol, timeframe="5m", limit=400)
    if not bars:
        return []

    now = int(time.time())
    rows: list[dict[str, Any]] = []
    for window_key, window_sec in G51_WINDOWS.items():
        chunk = _window_bars(bars, end_ts=anchor_ts, window_sec=window_sec)
        if not chunk:
            continue

        colors = [_candle_color(b) for b in chunk]
        pattern = "".join(colors[-8:])
        greens = colors.count("G")
        reds = colors.count("R")
        total = len(colors)
        bodies = [_body_pct(b) for b in chunk]
        wicks = [_wick_pct(b) for b in chunk]

        row = {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "anchor_ts": anchor_ts,
            "window_key": window_key,
            "pattern": pattern,
            "green_pct": round(greens / total * 100.0, 1) if total else 0.0,
            "red_pct": round(reds / total * 100.0, 1) if total else 0.0,
            "avg_body": round(sum(bodies) / len(bodies), 4) if bodies else 0.0,
            "avg_wick": round(sum(wicks) / len(wicks), 4) if wicks else 0.0,
            "largest_candle": round(_largest_candle_pct(chunk), 4),
            "created_at": now,
        }
        rows.append(row)

        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_TABLE} (
              candidate_id, symbol, anchor_ts, window_key, pattern,
              green_pct, red_pct, avg_body, avg_wick, largest_candle, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id, symbol, anchor_ts, window_key, row["pattern"],
                row["green_pct"], row["red_pct"], row["avg_body"], row["avg_wick"],
                row["largest_candle"], now,
            ),
        )
    return rows
