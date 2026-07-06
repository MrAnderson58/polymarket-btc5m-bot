"""Simulation orchestration and edge discovery."""

from __future__ import annotations

import sqlite3

from bot.research.market_behavior.observations import list_markets, load_market_path
from bot.research.strategy_simulator.config import (
    DISCOVER_DIRECTIONS,
    DISCOVER_MAX_DELTAS,
    DISCOVER_MAX_ENTRIES,
    DISCOVER_MAX_SPREADS,
    DISCOVER_MIN_DELTAS,
    DISCOVER_MIN_SECONDS,
    DISCOVER_TPS,
    MIN_OBS_PER_MARKET,
    MIN_TRADES_FOR_RANK,
)
from bot.research.strategy_simulator.simulator import VirtualTrade, simulate_strategy_on_market
from bot.research.strategy_simulator.statistics import SimulationStats, compute_stats
from bot.research.strategy_simulator.storage import store_discovered, store_simulation_stats
from bot.research.strategy_simulator.strategies import Strategy


def run_simulation(
    conn: sqlite3.Connection,
    strategy: Strategy,
    *,
    min_obs: int | None = None,
    market_limit: int | None = None,
    one_trade_per_market: bool = False,
    persist: bool = True,
) -> tuple[SimulationStats, list[VirtualTrade]]:
    slugs = list_markets(conn, min_obs=min_obs or MIN_OBS_PER_MARKET)
    if market_limit:
        slugs = slugs[:market_limit]

    all_trades: list[VirtualTrade] = []
    for slug in slugs:
        path = load_market_path(conn, slug)
        if len(path) < (min_obs or MIN_OBS_PER_MARKET):
            continue
        all_trades.extend(simulate_strategy_on_market(
            slug, path, strategy, one_trade_per_market=one_trade_per_market,
        ))

    stats = compute_stats(strategy, all_trades)
    if persist:
        store_simulation_stats(conn, stats)
    return stats, all_trades


def generate_discovery_grid() -> list[Strategy]:
    strategies: list[Strategy] = []
    for direction in DISCOVER_DIRECTIONS:
        for max_entry in DISCOVER_MAX_ENTRIES:
            for min_delta in DISCOVER_MIN_DELTAS:
                for max_delta in DISCOVER_MAX_DELTAS:
                    if direction == "YES" and max_delta is not None and max_delta < 0:
                        continue
                    if direction == "NO" and min_delta is not None and min_delta > 0:
                        continue
                    if min_delta is not None and max_delta is not None and min_delta > max_delta:
                        continue
                    for max_spread in DISCOVER_MAX_SPREADS:
                        for min_sec in DISCOVER_MIN_SECONDS:
                            for tp in DISCOVER_TPS:
                                if tp <= max_entry:
                                    continue
                                strategies.append(Strategy(
                                    direction=direction,
                                    max_entry=max_entry,
                                    min_delta=min_delta,
                                    max_delta=max_delta,
                                    max_spread=max_spread,
                                    min_seconds_left=min_sec,
                                    tp=tp,
                                ))
    return strategies


def run_discovery(
    conn: sqlite3.Connection,
    *,
    min_obs: int | None = None,
    market_limit: int | None = None,
    min_trades: int | None = None,
    top_n: int = 20,
    persist: bool = True,
) -> list[SimulationStats]:
    slugs = list_markets(conn, min_obs=min_obs or MIN_OBS_PER_MARKET)
    if market_limit:
        slugs = slugs[:market_limit]

    paths = {}
    for slug in slugs:
        path = load_market_path(conn, slug)
        if len(path) >= (min_obs or MIN_OBS_PER_MARKET):
            paths[slug] = path

    results: list[SimulationStats] = []
    for strategy in generate_discovery_grid():
        trades: list[VirtualTrade] = []
        for slug, path in paths.items():
            trades.extend(simulate_strategy_on_market(
                slug, path, strategy, one_trade_per_market=True,
            ))
        stats = compute_stats(strategy, trades)
        floor = min_trades or MIN_TRADES_FOR_RANK
        if stats.trades >= floor:
            results.append(stats)

    ranked = sorted(
        results,
        key=lambda s: (s.expected_value, s.profit_factor, s.win_rate, s.trades),
        reverse=True,
    )[:top_n]

    if persist:
        store_discovered(conn, ranked)
    return ranked
