"""Phase G.5.1 — liquidity metric evolution (funding/OI/volume/liquidations)."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.signal_intelligence.research_lake_types_g51 import (
    G51_LIQUIDITY_METRICS,
    G51_WINDOWS,
)

_TABLE = "market_events_liquidity_history_g51"

_METRIC_COL = {
    "funding": "funding",
    "oi": "open_interest",
    "liquidations": "liquidations",
    "volume": "volume",
}


def _format_evolution(values: list[float | None]) -> str:
    parts: list[str] = []
    prev: float | None = None
    for v in values:
        if v is None:
            continue
        label = f"{v:.4f}" if abs(v) < 1 else f"{v:.2f}"
        if prev is not None:
            arrow = "↓" if v < prev else "↑" if v > prev else "→"
            parts.append(f"{label}\n{arrow}")
        else:
            parts.append(label)
        prev = v
    return "\n".join(parts) if parts else ""


def _snapshot_series(conn: Any, *, anchor_ts: int, col: str) -> list[float | None]:
    values: list[float | None] = []
    for _key, window_sec in G51_WINDOWS.items():
        ts = anchor_ts - window_sec
        row = conn.execute(
            f"""
            SELECT {col} AS v FROM market_snapshots_g3
            WHERE snapshot_ts <= ?
            ORDER BY snapshot_ts DESC LIMIT 1
            """,
            (ts,),
        ).fetchone()
        values.append(float(row["v"]) if row and row["v"] is not None else None)
    return list(reversed(values))


def build_liquidity_history_g51(
    conn: Any,
    *,
    candidate_id: int,
    symbol: str,
    anchor_ts: int,
) -> list[dict[str, Any]]:
    now = int(time.time())
    rows: list[dict[str, Any]] = []
    for metric in G51_LIQUIDITY_METRICS:
        col = _METRIC_COL[metric]
        points = _snapshot_series(conn, anchor_ts=anchor_ts, col=col)
        if not any(p is not None for p in points):
            continue
        evolution = _format_evolution(points)
        row = {
            "candidate_id": candidate_id,
            "symbol": symbol,
            "metric": metric,
            "points_json": json.dumps(points),
            "evolution_text": evolution,
            "created_at": now,
        }
        rows.append(row)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {_TABLE} (
              candidate_id, symbol, metric, points_json, evolution_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, symbol, metric, row["points_json"], evolution, now),
        )
    return rows
