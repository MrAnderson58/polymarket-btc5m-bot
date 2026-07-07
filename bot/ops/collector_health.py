"""V4 collector health metrics for ops tooling."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from statistics import median

from bot.collector_diagnostics import MarketGapStats, _slug_window_start, analyze_market_gaps
from bot.research.features import load_market_observations


@dataclass
class CollectorHealth:
    last_observation_ts: int | None
    last_observation_age_sec: int | None
    completed_markets: list[MarketGapStats]
    median_obs_per_market: float | None
    median_gap_sec: float | None
    warnings: list[str]


def _is_completed_market(path: list[dict]) -> bool:
    if len(path) < 2:
        return False
    last = path[-1]
    seconds_left = last.get("seconds_left")
    if seconds_left is not None and int(seconds_left) <= 15:
        return True
    first_ts = int(path[0]["timestamp"])
    last_ts = int(path[-1]["timestamp"])
    return last_ts - first_ts >= 270


def analyze_collector_health(
    conn: sqlite3.Connection,
    *,
    completed_limit: int = 5,
) -> CollectorHealth:
    warnings: list[str] = []
    row = conn.execute("SELECT MAX(timestamp) AS ts FROM v4_shadow_observations").fetchone()
    last_ts = int(row["ts"]) if row and row["ts"] is not None else None
    age = int(time.time()) - last_ts if last_ts else None

    slug_rows = conn.execute(
        """
        SELECT market_slug, COUNT(*) AS n
        FROM v4_shadow_observations
        GROUP BY market_slug
        HAVING n >= 5
        ORDER BY market_slug
        """
    ).fetchall()
    slugs = sorted((r["market_slug"] for r in slug_rows), key=_slug_window_start)

    completed: list[MarketGapStats] = []
    for slug in reversed(slugs):
        path = load_market_observations(conn, slug)
        if not _is_completed_market(path):
            continue
        stats = analyze_market_gaps(path)
        if stats is not None:
            completed.append(stats)
        if len(completed) >= completed_limit:
            break
    completed.reverse()

    obs_counts = [m.obs_count for m in completed]
    gap_values = [m.median_gap_sec for m in completed if m.median_gap_sec > 0]
    median_obs = float(median(obs_counts)) if obs_counts else None
    median_gap = float(median(gap_values)) if gap_values else None

    if age is not None and age > 120:
        warnings.append(f"last V4 observation age {age}s > 120s")
    if median_gap is not None and median_gap > 5:
        warnings.append(f"median gap {median_gap:.1f}s > 5s on completed markets")
    if median_obs is not None and median_obs < 60:
        warnings.append(f"median obs/market {median_obs:.0f} < 60 on completed markets")

    return CollectorHealth(
        last_observation_ts=last_ts,
        last_observation_age_sec=age,
        completed_markets=completed,
        median_obs_per_market=median_obs,
        median_gap_sec=median_gap,
        warnings=warnings,
    )
