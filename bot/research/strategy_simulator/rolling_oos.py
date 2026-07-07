"""Rolling chronological out-of-sample validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from bot.research.strategy_simulator.discovery_core import (
    build_contexts,
    discover_on_paths,
    evaluate_strategies_on_paths,
)
from bot.research.strategy_simulator.grid import generate_discovery_grid
from bot.research.strategy_simulator.splits import sort_markets_chronologically
from bot.research.strategy_simulator.statistics import SimulationStats, compute_stats
from bot.research.strategy_simulator.strategies import Strategy


@dataclass
class RollingFoldResult:
    fold: int
    train_markets: int
    test_markets: int
    train_trades: int = 0
    train_ev: float = 0.0
    train_pf: float = 0.0
    test_trades: int = 0
    test_ev: float = 0.0
    test_pf: float = 0.0


@dataclass
class RollingOosSummary:
    strategy: Strategy
    folds: list[RollingFoldResult] = field(default_factory=list)
    positive_test_folds: int = 0
    weighted_oos_ev: float = 0.0
    total_oos_trades: int = 0
    oos_pf: float = 0.0
    worst_fold_ev: float = 0.0


def build_rolling_folds(
    paths: dict[str, list[dict]],
    *,
    n_folds: int = 5,
) -> list[tuple[list[str], list[str]]]:
    """Expanding train -> next chronological test block."""
    ordered = sort_markets_chronologically(paths)
    n = len(ordered)
    if n < n_folds + 1:
        return []

    segment_size = n // (n_folds + 1)
    segments: list[list[str]] = []
    for i in range(n_folds + 1):
        start = i * segment_size
        end = (i + 1) * segment_size if i < n_folds else n
        if start < end:
            segments.append(ordered[start:end])

    folds: list[tuple[list[str], list[str]]] = []
    for i in range(1, min(n_folds + 1, len(segments))):
        train = [s for seg in segments[:i] for s in seg]
        test = segments[i]
        if train and test:
            folds.append((train, test))
    return folds


def run_rolling_oos(
    all_paths: dict[str, list[dict]],
    strategies: list[Strategy],
    *,
    n_folds: int = 5,
    min_trades: int = 1,
) -> list[RollingOosSummary]:
    """Evaluate fixed strategies across rolling folds (no re-tuning inside fold)."""
    folds = build_rolling_folds(all_paths, n_folds=n_folds)
    if not folds:
        return []

    all_contexts = build_contexts(all_paths)
    summaries: list[RollingOosSummary] = []

    for strategy in strategies:
        summary = RollingOosSummary(strategy=strategy)
        test_pnls: list[float] = []
        weighted_ev_num = 0.0
        weighted_ev_den = 0.0
        gross_win = gross_loss = 0.0

        for fi, (train_slugs, test_slugs) in enumerate(folds, start=1):
            train_paths = {s: all_paths[s] for s in train_slugs}
            test_paths = {s: all_paths[s] for s in test_slugs}
            train_ctx = {s: all_contexts[s] for s in train_paths}
            test_ctx = {s: all_contexts[s] for s in test_paths}

            train_trades = evaluate_strategies_on_paths(
                train_paths, [strategy], contexts=train_ctx,
            ).get(strategy.fingerprint(), [])
            test_trades = evaluate_strategies_on_paths(
                test_paths, [strategy], contexts=test_ctx,
            ).get(strategy.fingerprint(), [])

            train_stats = compute_stats(strategy, train_trades)
            test_stats = compute_stats(strategy, test_trades)

            summary.folds.append(RollingFoldResult(
                fold=fi,
                train_markets=len(train_paths),
                test_markets=len(test_paths),
                train_trades=train_stats.trades,
                train_ev=train_stats.expected_value,
                train_pf=train_stats.profit_factor,
                test_trades=test_stats.trades,
                test_ev=test_stats.expected_value,
                test_pf=test_stats.profit_factor,
            ))

            if test_stats.expected_value > 0:
                summary.positive_test_folds += 1
            if test_trades:
                pnls = [t.pnl for t in test_trades]
                test_pnls.extend(pnls)
                weighted_ev_num += test_stats.expected_value * len(test_trades)
                weighted_ev_den += len(test_trades)
                gross_win += sum(p for p in pnls if p > 0)
                gross_loss += abs(sum(p for p in pnls if p <= 0))

        summary.total_oos_trades = len(test_pnls)
        summary.weighted_oos_ev = (
            weighted_ev_num / weighted_ev_den if weighted_ev_den else 0.0
        )
        summary.oos_pf = (
            gross_win / gross_loss if gross_loss > 0
            else (float("inf") if gross_win > 0 else 0.0)
        )
        fold_evs = [f.test_ev for f in summary.folds if f.test_trades > 0]
        summary.worst_fold_ev = min(fold_evs) if fold_evs else 0.0
        summaries.append(summary)

    return summaries


def discover_families_for_rolling(
    train_paths: dict[str, list[dict]],
    *,
    top_n: int,
    min_trades: int,
) -> list[Strategy]:
    """Discovery on initial train block only — for rolling we use pre-selected strategies."""
    reps, _, _ = discover_on_paths(
        train_paths,
        generate_discovery_grid(),
        min_trades=min_trades,
        top_n=top_n,
        dedupe=True,
        show_progress=False,
    )
    return [s.strategy for s in reps]
