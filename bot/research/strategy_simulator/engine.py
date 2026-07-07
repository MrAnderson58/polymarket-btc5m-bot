"""Simulation orchestration and edge discovery."""

from __future__ import annotations

import sqlite3
import sys

from bot.research.market_behavior.observations import list_markets, load_market_path
from bot.research.strategy_simulator.config import MIN_OBS_PER_MARKET
from bot.research.strategy_simulator.discovery_core import discover_on_paths
from bot.research.strategy_simulator.deduplication import StrategyFamily
from bot.research.strategy_simulator.grid import generate_discovery_grid
from bot.research.strategy_simulator.market_filter import MarketFilter, list_filtered_market_paths
from bot.research.strategy_simulator.simulator import (
    VirtualTrade,
    build_market_context,
    simulate_strategy_on_context,
)
from bot.research.strategy_simulator.statistics import SimulationStats, compute_stats
from bot.research.strategy_simulator.storage import store_discovered, store_simulation_stats
from bot.research.strategy_simulator.strategies import Strategy

# Re-export for backwards compatibility.
__all__ = [
    "generate_discovery_grid",
    "run_discovery",
    "run_simulation",
    "profile_discovery",
]


def run_simulation(
    conn: sqlite3.Connection,
    strategy: Strategy,
    *,
    min_obs: int | None = None,
    market_limit: int | None = None,
    max_markets: int | None = None,
    one_trade_per_market: bool = False,
    persist: bool = True,
    market_filter: MarketFilter | None = None,
) -> tuple[SimulationStats, list[VirtualTrade]]:
    cap = market_limit or max_markets
    if market_filter is not None:
        paths = list_filtered_market_paths(conn, market_filter, base_min_obs=min_obs)
        slugs = list(paths.keys())
    else:
        slugs = list_markets(conn, min_obs=min_obs or MIN_OBS_PER_MARKET)
        paths = None
    if cap:
        slugs = slugs[:cap]

    all_trades: list[VirtualTrade] = []
    for slug in slugs:
        path = paths[slug] if paths is not None else load_market_path(conn, slug)
        if len(path) < (min_obs or MIN_OBS_PER_MARKET):
            continue
        ctx = build_market_context(slug, path, tp_levels=(strategy.tp,))
        all_trades.extend(simulate_strategy_on_context(
            ctx, strategy, one_trade_per_market=one_trade_per_market,
        ))

    stats = compute_stats(strategy, all_trades)
    if persist:
        store_simulation_stats(conn, stats)
    return stats, all_trades


def run_discovery(
    conn: sqlite3.Connection,
    *,
    min_obs: int | None = None,
    market_limit: int | None = None,
    max_markets: int | None = None,
    min_trades: int | None = None,
    top_n: int = 20,
    persist: bool = True,
    show_progress: bool = True,
    dedupe: bool = True,
    market_filter: MarketFilter | None = None,
) -> tuple[list[SimulationStats], list[StrategyFamily]]:
    cap = market_limit or max_markets
    if market_filter is not None:
        paths = list_filtered_market_paths(conn, market_filter, base_min_obs=min_obs)
        if cap:
            paths = dict(list(paths.items())[:cap])
    else:
        slugs = list_markets(conn, min_obs=min_obs or MIN_OBS_PER_MARKET)
        if cap:
            slugs = slugs[:cap]
        paths = {}
        for slug in slugs:
            path = load_market_path(conn, slug)
            if len(path) >= (min_obs or MIN_OBS_PER_MARKET):
                paths[slug] = path

    ranked, _, families = discover_on_paths(
        paths,
        generate_discovery_grid(),
        min_trades=min_trades,
        top_n=top_n,
        dedupe=dedupe,
        show_progress=show_progress,
    )

    if persist:
        store_discovered(conn, ranked)
    return ranked, families


def profile_discovery(
    conn: sqlite3.Connection,
    *,
    max_markets: int = 3,
    top_n: int = 5,
) -> None:
    """Profile run_discovery and print top slowest functions."""
    import cProfile
    import io
    import pstats

    pr = cProfile.Profile()
    pr.enable()
    run_discovery(
        conn,
        max_markets=max_markets,
        min_trades=1,
        top_n=top_n,
        persist=False,
        show_progress=False,
    )
    pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(25)
    sys.stdout.write(s.getvalue())
