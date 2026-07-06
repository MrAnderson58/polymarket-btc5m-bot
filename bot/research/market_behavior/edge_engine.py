"""Run multidimensional edge analysis over historical observations."""

from __future__ import annotations

import sqlite3

from bot.research.market_behavior.config import MIN_OBS_PER_MARKET
from bot.research.market_behavior.edge_finder import EdgeCell, aggregate_edge_observations, find_all_combinations
from bot.research.market_behavior.edge_statistics import collect_edge_observations
from bot.research.market_behavior.edge_storage import store_edge_cells
from bot.research.market_behavior.observations import list_markets, load_market_path


def run_edge_analysis(
    conn: sqlite3.Connection,
    *,
    min_obs: int | None = None,
    market_limit: int | None = None,
    persist: bool = True,
) -> list[EdgeCell]:
    slugs = list_markets(conn, min_obs=min_obs or MIN_OBS_PER_MARKET)
    if market_limit is not None:
        slugs = slugs[:market_limit]

    all_edge_obs = []
    for slug in slugs:
        path = load_market_path(conn, slug)
        if len(path) < (min_obs or MIN_OBS_PER_MARKET):
            continue
        all_edge_obs.extend(collect_edge_observations(path))

    cells = find_all_combinations(aggregate_edge_observations(all_edge_obs))
    if persist:
        store_edge_cells(conn, cells)
    return cells
