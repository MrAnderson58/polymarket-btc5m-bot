"""Forward outcome evaluation for parsed futures signals."""

from __future__ import annotations

import sqlite3
from typing import Any

import requests

from bot.research.futures.config import (
    BINANCE_SPOT_API,
    OUTCOME_HORIZONS,
    STANDARDIZED_RISK_PCT,
    STANDARDIZED_RR,
)
from bot.research.futures.market_snapshot import _fetch_klines, _symbol_pair
from bot.research.futures.schema import OUTCOMES_TABLE, SIGNALS_TABLE, TARGETS_TABLE

REQUEST_TIMEOUT = 8


def _entry_price(row: sqlite3.Row, tps: list[float]) -> float | None:
    if row["entry_min"] is not None and row["entry_max"] is not None:
        return (float(row["entry_min"]) + float(row["entry_max"])) / 2.0
    if row["entry_min"] is not None:
        return float(row["entry_min"])
    if tps:
        return None
    return None


def _path_metrics(
    candles: list[list],
    start_ts: int,
    end_ts: int,
    side: str,
    entry: float,
) -> dict[str, Any]:
    path = [c for c in candles if start_ts <= int(c[0] // 1000) <= end_ts]
    if not path:
        return {}
    highs = [float(c[2]) for c in path]
    lows = [float(c[3]) for c in path]
    end_price = float(path[-1][4])
    fwd = (end_price - entry) / entry * 100.0
    if side == "SHORT":
        fwd = -fwd
        mfe = (entry - min(lows)) / entry * 100.0
        mae = (max(highs) - entry) / entry * 100.0
    else:
        mfe = (max(highs) - entry) / entry * 100.0
        mae = (entry - min(lows)) / entry * 100.0
    return {
        "forward_return": fwd,
        "direction_correct": int(fwd > 0),
        "mfe": mfe,
        "mae": mae,
        "end_price": end_price,
        "highs": highs,
        "lows": lows,
        "path": path,
    }


def _tp_sl_race(
    path: list[list],
    side: str,
    entry: float,
    sl: float | None,
    tps: list[float],
) -> dict[str, Any | None]:
    tp_first = sl_first = None
    time_to_tp = time_to_sl = None
    start_ts = int(path[0][0] // 1000) if path else None

    for c in path:
        ts = int(c[0] // 1000)
        high, low = float(c[2]), float(c[3])
        if side == "LONG":
            if sl_first is None and sl is not None and low <= sl:
                sl_first, time_to_sl = 1, ts - start_ts if start_ts else None
            for i, tp in enumerate(tps):
                if tp_first is None and high >= tp:
                    tp_first, time_to_tp = 1, ts - start_ts if start_ts else None
                    break
        else:
            if sl_first is None and sl is not None and high >= sl:
                sl_first, time_to_sl = 1, ts - start_ts if start_ts else None
            for i, tp in enumerate(tps):
                if tp_first is None and low <= tp:
                    tp_first, time_to_tp = 1, ts - start_ts if start_ts else None
                    break
    return {
        "tp_first": tp_first,
        "sl_first": sl_first,
        "time_to_tp": time_to_tp,
        "time_to_sl": time_to_sl,
    }


def _pnl_explicit(side: str, entry: float, sl: float | None, tps: list[float], outcome: dict) -> float | None:
    if sl is None or not tps:
        return None
    race = _tp_sl_race(outcome.get("path", []), side, entry, sl, tps)
    if race.get("tp_first"):
        tp = tps[0]
        return abs(tp - entry) / entry * 100.0
    if race.get("sl_first"):
        return -abs(entry - sl) / entry * 100.0
    return outcome.get("forward_return")


def _pnl_standardized(forward_return: float | None, direction_correct: int | None) -> float | None:
    if forward_return is None:
        return None
    if direction_correct:
        return STANDARDIZED_RR * STANDARDIZED_RISK_PCT
    return -STANDARDIZED_RISK_PCT


def evaluate_signal_outcomes(conn: sqlite3.Connection, signal_id: int) -> int:
    row = conn.execute(f"SELECT * FROM {SIGNALS_TABLE} WHERE id = ?", (signal_id,)).fetchone()
    if not row or not row["symbol"] or not row["side"]:
        return 0
    tps = [
        float(r["price"])
        for r in conn.execute(
            f"SELECT price FROM {TARGETS_TABLE} WHERE signal_id = ? ORDER BY level_index",
            (signal_id,),
        ).fetchall()
    ]
    ts = int(row["timestamp"])
    pair = _symbol_pair(row["symbol"])
    max_horizon = max(OUTCOME_HORIZONS.values())
    candles = _fetch_klines(BINANCE_SPOT_API, pair, "1m", ts + max_horizon, limit=1500)
    entry = _entry_price(row, tps)
    if entry is None:
        # Use first available close at signal time as research entry
        from bot.research.futures.market_snapshot import _close_at
        entry = _close_at(candles, ts)
    if entry is None:
        return 0

    n = 0
    for horizon, sec in OUTCOME_HORIZONS.items():
        end_ts = ts + sec
        metrics = _path_metrics(candles, ts, end_ts, row["side"], entry)
        if not metrics:
            continue
        race = _tp_sl_race(metrics.get("path", []), row["side"], entry, row["stop_loss"], tps)
        pnl_e = _pnl_explicit(row["side"], entry, row["stop_loss"], tps, metrics)
        pnl_s = _pnl_standardized(metrics["forward_return"], metrics["direction_correct"])
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {OUTCOMES_TABLE} (
                signal_id, horizon, forward_return, direction_correct,
                mfe, mae, tp_first, sl_first, time_to_tp, time_to_sl,
                pnl_explicit, pnl_standardized, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, horizon,
                metrics["forward_return"], metrics["direction_correct"],
                metrics["mfe"], metrics["mae"],
                race.get("tp_first"), race.get("sl_first"),
                race.get("time_to_tp"), race.get("time_to_sl"),
                pnl_e, pnl_s, end_ts,
            ),
        )
        n += 1
    return n


def evaluate_all_outcomes(conn: sqlite3.Connection, *, limit: int | None = None) -> dict[str, int]:
    q = f"SELECT id FROM {SIGNALS_TABLE} WHERE side IS NOT NULL AND symbol IS NOT NULL ORDER BY timestamp"
    if limit:
        q += f" LIMIT {int(limit)}"
    ids = [r["id"] for r in conn.execute(q).fetchall()]
    stats = {"signals": len(ids), "horizons_written": 0}
    for sid in ids:
        stats["horizons_written"] += evaluate_signal_outcomes(conn, sid)
    return stats
