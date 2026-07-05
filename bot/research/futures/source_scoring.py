"""Per-source/channel performance scoring."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.futures.config import MIN_SOURCE_N, MIN_SYMBOL_N
from bot.research.futures.schema import OUTCOMES_TABLE, SIGNALS_TABLE, SNAPSHOTS_TABLE


def _pf(values: list[float]) -> float:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return wins if wins > 0 else 0.0
    return wins / losses


def _max_dd(values: list[float]) -> float:
    eq = peak = 0.0
    max_dd = 0.0
    for v in values:
        eq += v
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return max_dd


def _max_cl(values: list[float]) -> int:
    cl = max_cl = 0
    for v in values:
        if v < 0:
            cl += 1
            max_cl = max(max_cl, cl)
        else:
            cl = 0
    return max_cl


def score_sources(conn: sqlite3.Connection, *, horizon: str = "1h") -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT s.source, s.symbol, s.side, o.pnl_standardized, o.direction_correct,
               o.mfe, o.mae, snap.features_json
        FROM {SIGNALS_TABLE} s
        JOIN {OUTCOMES_TABLE} o ON o.signal_id = s.id AND o.horizon = ?
        LEFT JOIN {SNAPSHOTS_TABLE} snap ON snap.signal_id = s.id
        WHERE o.pnl_standardized IS NOT NULL
        ORDER BY s.timestamp
        """,
        (horizon,),
    ).fetchall()

    by_source: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)

    leaderboard = []
    for source, items in by_source.items():
        pnls = [float(r["pnl_standardized"]) for r in items]
        n = len(pnls)
        entry: dict[str, Any] = {
            "source": source,
            "n": n,
            "sufficient_sample": n >= MIN_SOURCE_N,
            "directional_accuracy": sum(int(r["direction_correct"]) for r in items) / n,
            "pf": _pf(pnls),
            "avg_pnl": sum(pnls) / n,
            "median_pnl": sorted(pnls)[n // 2],
            "max_dd": _max_dd(pnls),
            "max_cl": _max_cl(pnls),
            "avg_mfe": sum(float(r["mfe"] or 0) for r in items) / n,
            "avg_mae": sum(float(r["mae"] or 0) for r in items) / n,
        }
        entry["by_side"] = _group_stats(items, "side", MIN_SYMBOL_N)
        entry["by_symbol"] = _group_stats(items, "symbol", MIN_SYMBOL_N)
        entry["rolling_pf"] = _rolling_pf(pnls)
        leaderboard.append(entry)

    leaderboard.sort(key=lambda x: (-int(x["sufficient_sample"]), -x["pf"]))
    return leaderboard


def _group_stats(items: list[sqlite3.Row], key: str, min_n: int) -> dict[str, Any]:
    groups: dict[str, list[float]] = {}
    for r in items:
        k = r[key] or "unknown"
        groups.setdefault(k, []).append(float(r["pnl_standardized"]))
    out = {}
    for k, pnls in groups.items():
        if len(pnls) < min_n:
            out[k] = {"n": len(pnls), "sufficient_sample": False}
            continue
        out[k] = {
            "n": len(pnls),
            "sufficient_sample": True,
            "pf": _pf(pnls),
            "avg_pnl": sum(pnls) / len(pnls),
        }
    return out


def _rolling_pf(pnls: list[float], window: int = 20) -> list[float]:
    if len(pnls) < window:
        return []
    out = []
    for i in range(window, len(pnls) + 1):
        out.append(_pf(pnls[i - window:i]))
    return out
