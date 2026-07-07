"""Prepare forward shadow validation from dense-era discovery."""

from __future__ import annotations

import sqlite3

from bot.research.strategy_simulator.dense_era import detect_dense_era_boundary
from bot.research.strategy_simulator.engine import run_discovery
from bot.research.strategy_simulator.forward_tracker import register_top_families
from bot.research.strategy_simulator.market_filter import MarketFilter


def prepare_forward_candidates(
    conn: sqlite3.Connection,
    *,
    min_obs: int = 60,
    max_median_gap: float = 5.0,
    min_span: int = 240,
    consecutive_required: int = 5,
    top_families: int = 3,
    discovery_top_n: int = 20,
    min_trades: int | None = None,
) -> tuple[object, list[str]]:
    """Discover on homogeneous dense-era markets; register <=3 family reps."""
    boundary = detect_dense_era_boundary(
        conn,
        min_obs=min_obs,
        max_median_gap=max_median_gap,
        min_span=min_span,
        consecutive_required=consecutive_required,
    )
    if boundary.boundary_window_start_ts is None:
        raise RuntimeError("dense-era boundary not found in database")

    filt = MarketFilter(
        market_start_ts=boundary.boundary_window_start_ts,
        min_obs_per_market=min_obs,
        max_median_gap=max_median_gap,
        min_coverage_span=min_span,
        completed_only=True,
    )
    _, families = run_discovery(
        conn,
        market_filter=filt,
        min_trades=min_trades,
        top_n=discovery_top_n,
        persist=True,
        show_progress=False,
        dedupe=True,
    )
    fps = register_top_families(conn, families, max_families=top_families)
    return boundary, fps
