"""Dense-era detection from v4_shadow_observations market quality."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from statistics import median

from bot.collector_diagnostics import MarketGapStats, _slug_window_start, analyze_market_gaps
from bot.research.features import load_market_observations


@dataclass(frozen=True)
class CompletedMarketQuality:
    market_slug: str
    window_start_ts: int
    obs_count: int
    median_gap_sec: float
    coverage_span_sec: int
    first_obs_ts: int
    passes_dense: bool


@dataclass(frozen=True)
class DenseEraBoundary:
    """First market window_start_ts where dense-era criteria hold consistently."""

    boundary_window_start_ts: int | None
    boundary_first_obs_ts: int | None
    consecutive_markets_required: int
    markets_analyzed: int
    dense_market_count: int
    first_dense_market: CompletedMarketQuality | None
    stable_run_start: CompletedMarketQuality | None


def _is_completed_market(path: list[dict], *, min_span: int = 240) -> bool:
    if len(path) < 2:
        return False
    last = path[-1]
    seconds_left = last.get("seconds_left")
    if seconds_left is not None and int(seconds_left) <= 15:
        return True
    first_ts = int(path[0]["timestamp"])
    last_ts = int(path[-1]["timestamp"])
    return last_ts - first_ts >= min_span


def assess_completed_market(
    path: list[dict],
    *,
    min_obs: int = 60,
    max_median_gap: float = 5.0,
    min_span: int = 240,
) -> CompletedMarketQuality | None:
    if not _is_completed_market(path, min_span=min_span):
        return None
    stats = analyze_market_gaps(path)
    if stats is None:
        return None
    passes = (
        stats.obs_count >= min_obs
        and stats.median_gap_sec <= max_median_gap
        and stats.collector_span_sec >= min_span
    )
    return CompletedMarketQuality(
        market_slug=stats.market_slug,
        window_start_ts=stats.window_start_ts,
        obs_count=stats.obs_count,
        median_gap_sec=stats.median_gap_sec,
        coverage_span_sec=stats.collector_span_sec,
        first_obs_ts=stats.first_ts,
        passes_dense=passes,
    )


def list_completed_market_quality(
    conn: sqlite3.Connection,
    *,
    min_obs_floor: int = 5,
    min_obs: int = 60,
    max_median_gap: float = 5.0,
    min_span: int = 240,
) -> list[CompletedMarketQuality]:
    rows = conn.execute(
        """
        SELECT market_slug
        FROM v4_shadow_observations
        GROUP BY market_slug
        HAVING COUNT(*) >= ?
        ORDER BY market_slug
        """,
        (min_obs_floor,),
    ).fetchall()
    slugs = sorted((r["market_slug"] for r in rows), key=_slug_window_start)
    out: list[CompletedMarketQuality] = []
    for slug in slugs:
        path = load_market_observations(conn, slug)
        quality = assess_completed_market(
            path,
            min_obs=min_obs,
            max_median_gap=max_median_gap,
            min_span=min_span,
        )
        if quality is not None:
            out.append(quality)
    return out


def detect_dense_era_boundary(
    conn: sqlite3.Connection,
    *,
    min_obs: int = 60,
    max_median_gap: float = 5.0,
    min_span: int = 240,
    consecutive_required: int = 5,
) -> DenseEraBoundary:
    """Find first window_start where ``consecutive_required`` completed markets pass."""
    qualities = list_completed_market_quality(
        conn,
        min_obs=min_obs,
        max_median_gap=max_median_gap,
        min_span=min_span,
    )
    dense_markets = [q for q in qualities if q.passes_dense]
    first_dense = dense_markets[0] if dense_markets else None

    stable_start: CompletedMarketQuality | None = None
    boundary_ws: int | None = None
    boundary_first_obs: int | None = None

    run = 0
    for q in qualities:
        if q.passes_dense:
            run += 1
            if run >= consecutive_required and stable_start is None:
                stable_start = q
                # boundary = window_start of market that started the stable run
                idx = qualities.index(q) - consecutive_required + 1
                if idx >= 0:
                    boundary_ws = qualities[idx].window_start_ts
                    boundary_first_obs = qualities[idx].first_obs_ts
        else:
            run = 0

    return DenseEraBoundary(
        boundary_window_start_ts=boundary_ws,
        boundary_first_obs_ts=boundary_first_obs,
        consecutive_markets_required=consecutive_required,
        markets_analyzed=len(qualities),
        dense_market_count=len(dense_markets),
        first_dense_market=first_dense,
        stable_run_start=stable_start,
    )


def render_dense_era_report(boundary: DenseEraBoundary) -> str:
    lines = [
        "DENSE ERA BOUNDARY (from v4_shadow_observations)",
        f"  completed markets analyzed: {boundary.markets_analyzed}",
        f"  dense markets (pass all thresholds): {boundary.dense_market_count}",
        f"  consecutive required: {boundary.consecutive_markets_required}",
    ]
    if boundary.first_dense_market:
        m = boundary.first_dense_market
        lines.append(
            f"  first dense market: {m.market_slug} ws={m.window_start_ts} "
            f"obs={m.obs_count} gap={m.median_gap_sec:.1f}s span={m.coverage_span_sec}s"
        )
    if boundary.boundary_window_start_ts is not None:
        lines.append(
            f"  stable dense-era boundary window_start_ts: {boundary.boundary_window_start_ts}"
        )
        lines.append(
            f"  boundary first observation ts: {boundary.boundary_first_obs_ts}"
        )
    else:
        lines.append("  stable dense-era boundary: NOT FOUND")
    if boundary.stable_run_start:
        s = boundary.stable_run_start
        lines.append(
            f"  stable run confirmed at: {s.market_slug} ws={s.window_start_ts}"
        )
    return "\n".join(lines)


def dense_era_summary_stats(qualities: list[CompletedMarketQuality]) -> dict[str, float | int]:
    dense = [q for q in qualities if q.passes_dense]
    if not dense:
        return {"count": 0}
    return {
        "count": len(dense),
        "median_obs": float(median([q.obs_count for q in dense])),
        "median_gap": float(median([q.median_gap_sec for q in dense])),
        "median_span": float(median([q.coverage_span_sec for q in dense])),
    }
