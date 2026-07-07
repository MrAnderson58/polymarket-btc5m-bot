"""Shared discovery engine with context caching."""

from __future__ import annotations

from collections import defaultdict

from bot.research.strategy_simulator.config import DISCOVER_TPS, MIN_TRADES_FOR_RANK
from bot.research.strategy_simulator.deduplication import StrategyFamily, deduplicate_stats_list
from bot.research.strategy_simulator.market_context import MarketPathContext
from bot.research.strategy_simulator.progress import DiscoveryProgress
from bot.research.strategy_simulator.simulator import VirtualTrade, simulate_strategy_on_context
from bot.research.strategy_simulator.statistics import SimulationStats, compute_stats
from bot.research.strategy_simulator.strategies import Strategy


def build_contexts(paths: dict[str, list[dict]]) -> dict[str, MarketPathContext]:
    return {
        slug: MarketPathContext.build(slug, path, tp_levels=DISCOVER_TPS)
        for slug, path in paths.items()
    }


def discover_on_paths(
    paths: dict[str, list[dict]],
    strategies: list[Strategy],
    *,
    contexts: dict[str, MarketPathContext] | None = None,
    min_trades: int | None = None,
    top_n: int = 20,
    dedupe: bool = True,
    show_progress: bool = False,
) -> tuple[list[SimulationStats], dict[str, list[VirtualTrade]], list[StrategyFamily]]:
    """Run discovery on a fixed path set; reuse prebuilt contexts when provided."""
    ctx_map = contexts or build_contexts(paths)
    trades_by_fp: dict[str, list[VirtualTrade]] = defaultdict(list)
    progress = DiscoveryProgress(
        total_markets=len(paths),
        total_strategies=len(strategies),
    ) if show_progress else None

    for mi, (slug, _) in enumerate(paths.items(), start=1):
        ctx = ctx_map[slug]
        if progress:
            progress.set_market(mi)
        for si, strategy in enumerate(strategies, start=1):
            trades_by_fp[strategy.fingerprint()].extend(
                simulate_strategy_on_context(ctx, strategy, one_trade_per_market=True),
            )
            if progress:
                progress.tick_strategy(si)

    if progress:
        progress.finish()

    floor = min_trades or MIN_TRADES_FOR_RANK
    results: list[SimulationStats] = []
    for strategy in strategies:
        trades = trades_by_fp[strategy.fingerprint()]
        stats = compute_stats(strategy, trades)
        if stats.trades >= floor:
            results.append(stats)

    ranked = sorted(
        results,
        key=lambda s: (s.expected_value, s.profit_factor, s.win_rate, s.trades),
        reverse=True,
    )

    families: list[StrategyFamily] = []
    if dedupe:
        reps, families = deduplicate_stats_list(ranked, trades_by_fp, top_n=top_n)
        return reps, trades_by_fp, families

    return ranked[:top_n], trades_by_fp, families


def evaluate_strategies_on_paths(
    paths: dict[str, list[dict]],
    strategies: list[Strategy],
    *,
    contexts: dict[str, MarketPathContext] | None = None,
) -> dict[str, list[VirtualTrade]]:
    """Evaluate fixed strategies without discovery; reuse contexts."""
    ctx_map = contexts or build_contexts(paths)
    trades_by_fp: dict[str, list[VirtualTrade]] = defaultdict(list)
    for slug in paths:
        ctx = ctx_map[slug]
        for strategy in strategies:
            trades_by_fp[strategy.fingerprint()].extend(
                simulate_strategy_on_context(ctx, strategy, one_trade_per_market=True),
            )
    return trades_by_fp
