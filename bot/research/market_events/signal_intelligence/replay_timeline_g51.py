"""Phase G.5.1 — full replay timeline after signal appearance."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.candles import CandleBar, load_recent_candles
from bot.research.market_events.signal_intelligence.research_lake_types_g51 import G51_REPLAY_WINDOWS

_TABLE = "market_events_replay_timeline_g51"


def _price_at(bars: list[CandleBar], ts: int) -> float | None:
    best = None
    for b in bars:
        if b.open_ts <= ts:
            best = b
        else:
            break
    return float(best.close) if best else None


def _pnl_pct(entry: float, price: float, *, is_long: bool) -> float:
    if entry <= 0:
        return 0.0
    if is_long:
        return (price / entry - 1.0) * 100.0
    return (1.0 - price / entry) * 100.0


def build_replay_timeline_g51(
    conn: Any,
    *,
    candidate_id: int,
    symbol: str,
    anchor_ts: int,
    direction: str | None,
    entry_price: float | None = None,
) -> list[dict[str, Any]]:
    bars = load_recent_candles(conn, symbol=symbol, timeframe="5m", limit=500)
    if not bars:
        return []

    entry = entry_price
    if entry is None or entry <= 0:
        entry = _price_at(bars, anchor_ts)
    if not entry or entry <= 0:
        return []

    is_long = str(direction or "LONG").upper() == "LONG"
    now = int(time.time())
    rows: list[dict[str, Any]] = []
    max_profit = 0.0
    max_dd = 0.0

    for window_key, offset_sec in G51_REPLAY_WINDOWS.items():
        ts = anchor_ts + offset_sec
        price = _price_at(bars, ts)
        if price is None:
            continue
        pnl = round(_pnl_pct(entry, price, is_long=is_long), 4)
        max_profit = max(max_profit, pnl)
        max_dd = min(max_dd, pnl)
        row = {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "direction": direction,
            "window_key": window_key,
            "price": price,
            "pnl_pct": pnl,
            "max_profit_so_far": round(max_profit, 4),
            "drawdown_so_far": round(max_dd, 4),
            "created_at": now,
        }
        rows.append(row)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_TABLE} (
              candidate_id, symbol, direction, window_key, price, pnl_pct,
              max_profit_so_far, drawdown_so_far, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id, symbol, direction, window_key, price, pnl,
                row["max_profit_so_far"], row["drawdown_so_far"], now,
            ),
        )
    return rows


def format_replay_evolution(timeline: list[dict[str, Any]]) -> str:
    if not timeline:
        return ""
    parts = []
    for row in timeline:
        parts.append(f"{row['window_key']} pnl={row.get('pnl_pct', 0):+.2f}%")
    return " | ".join(parts)
