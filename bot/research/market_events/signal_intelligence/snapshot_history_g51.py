"""Phase G.5.1 — snapshot history builder per candidate."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    compute_atr,
    load_recent_candles,
)
from bot.research.market_events.signal_intelligence.research_lake_types_g51 import G51_WINDOWS

_TABLE = "market_events_snapshot_history_g51"


def _price_at(bars: list[CandleBar], ts: int) -> float | None:
    best = None
    for b in bars:
        if b.open_ts <= ts:
            best = b
        else:
            break
    return float(best.close) if best else None


def _window_bars(bars: list[CandleBar], *, end_ts: int, window_sec: int) -> list[CandleBar]:
    start = end_ts - window_sec
    return [b for b in bars if start <= b.open_ts <= end_ts]


def _nearest_snapshot(conn: Any, ts: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT funding, open_interest AS oi, liquidations, volume, atr,
               btc_price, eth_price, btc_dominance AS dominance, fear_greed, snapshot_ts
        FROM market_snapshots_g3
        WHERE snapshot_ts <= ?
        ORDER BY snapshot_ts DESC LIMIT 1
        """,
        (ts,),
    ).fetchone()
    return dict(row) if row else None


def _avg_snapshot_field(conn: Any, *, start_ts: int, end_ts: int, field: str) -> float | None:
    row = conn.execute(
        f"""
        SELECT AVG({field}) AS v FROM market_snapshots_g3
        WHERE snapshot_ts BETWEEN ? AND ?
        """,
        (start_ts, end_ts),
    ).fetchone()
    if not row or row["v"] is None:
        return None
    return float(row["v"])


def build_snapshot_history_g51(
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
        end_ts = anchor_ts
        start_ts = anchor_ts - window_sec
        chunk = _window_bars(bars, end_ts=end_ts, window_sec=window_sec)
        price_start = _price_at(bars, start_ts) or (chunk[0].open if chunk else None)
        price_end = _price_at(bars, end_ts) or (chunk[-1].close if chunk else None)
        ret = None
        if price_start and price_end and price_start > 0:
            ret = round((price_end / price_start - 1.0) * 100.0, 4)

        vol = round(sum(b.volume for b in chunk), 4) if chunk else None
        atr = round(compute_atr(chunk), 6) if len(chunk) >= 2 else None

        snap = _nearest_snapshot(conn, end_ts) or {}
        funding = _avg_snapshot_field(conn, start_ts=start_ts, end_ts=end_ts, field="funding")
        oi = _avg_snapshot_field(conn, start_ts=start_ts, end_ts=end_ts, field="open_interest")
        liq = _avg_snapshot_field(conn, start_ts=start_ts, end_ts=end_ts, field="liquidations")

        row = {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "anchor_ts": anchor_ts,
            "window_key": window_key,
            "price": price_end,
            "return_pct": ret,
            "volume": vol,
            "atr": atr,
            "funding": funding if funding is not None else snap.get("funding"),
            "oi": oi if oi is not None else snap.get("oi"),
            "liquidations": liq if liq is not None else snap.get("liquidations"),
            "btc_price": snap.get("btc_price"),
            "eth_price": snap.get("eth_price"),
            "dominance": snap.get("dominance"),
            "fear_greed": snap.get("fear_greed"),
            "created_at": now,
        }
        rows.append(row)

        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_TABLE} (
              candidate_id, symbol, anchor_ts, window_key, price, return_pct, volume, atr,
              funding, oi, liquidations, btc_price, eth_price, dominance, fear_greed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id, symbol, anchor_ts, window_key,
                row["price"], row["return_pct"], row["volume"], row["atr"],
                row["funding"], row["oi"], row["liquidations"],
                row["btc_price"], row["eth_price"], row["dominance"], row["fear_greed"],
                now,
            ),
        )
    return rows
