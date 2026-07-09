"""Observation data summary report."""

from __future__ import annotations

import json
from typing import Any


def observation_report(conn: Any, *, days: int = 7) -> str:
    since = int(__import__("time").time()) - days * 86400
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_price_observations WHERE obs_ts >= ?",
        (since,),
    ).fetchone()["n"]

    rows = conn.execute(
        """
        SELECT i.canonical_asset, i.venue_symbol, COUNT(*) AS n,
               MIN(o.obs_ts) AS first_ts, MAX(o.obs_ts) AS last_ts,
               AVG(o.spread_bps) AS avg_spread,
               AVG(ABS(o.basis_bps)) AS avg_abs_basis
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.obs_ts >= ?
        GROUP BY i.canonical_asset, i.venue_symbol
        ORDER BY n DESC
        """,
        (since,),
    ).fetchall()

    status_counts = {"inserted": 0, "fetch_failed": 0, "stale": 0, "skipped": 0}
    raw_rows = conn.execute(
        "SELECT raw_json FROM market_events_price_observations WHERE obs_ts >= ?",
        (since,),
    ).fetchall()
    for r in raw_rows:
        try:
            meta = json.loads(r["raw_json"] or "{}")
            st = meta.get("observe_status", "inserted")
            status_counts[st] = status_counts.get(st, 0) + 1
        except Exception:
            status_counts["inserted"] = status_counts.get("inserted", 0) + 1

    lines = [
        "OBSERVATION REPORT",
        f"window_days: {days}",
        f"total_observations: {total}",
        f"by_status: {status_counts}",
        "",
        "=== BY SYMBOL ===",
    ]
    if not rows:
        lines.append("  (no observations in window — run observe-run --universe tradfi-observe)")
    for r in rows:
        avg_sp = float(r["avg_spread"] or 0)
        avg_basis = float(r["avg_abs_basis"] or 0)
        lines.append(
            f"  {r['canonical_asset']} ({r['venue_symbol']}): n={r['n']} "
            f"first={r['first_ts']} last={r['last_ts']} "
            f"avg_spread_bps={avg_sp:.2f} avg_abs_basis_bps={avg_basis:.2f}",
        )
    return "\n".join(lines)
