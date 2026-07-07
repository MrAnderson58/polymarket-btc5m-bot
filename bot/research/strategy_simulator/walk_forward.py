"""Walk-forward out-of-sample validation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from bot.research.market_behavior.observations import list_markets, load_market_path
from bot.research.strategy_simulator.bootstrap import BootstrapResult, bootstrap_market_metrics
from bot.research.strategy_simulator.config import (
    BOOTSTRAP_MIN_PROB_EV_POSITIVE,
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    MIN_FINALIST_TEST_TRADES,
    MIN_OBS_PER_MARKET,
)
from bot.research.strategy_simulator.cost_model import (
    COST_SCENARIOS,
    apply_cost_model_filtered,
    apply_costs_same_trade_set,
)
from bot.research.strategy_simulator.deduplication import StrategyFamily
from bot.research.strategy_simulator.discovery_core import (
    build_contexts,
    discover_on_paths,
    evaluate_strategies_on_paths,
)
from bot.research.strategy_simulator.grid import generate_discovery_grid
from bot.research.strategy_simulator.splits import MarketSplit, split_markets_chronological
from bot.research.strategy_simulator.statistics import SimulationStats, compute_stats
from bot.research.strategy_simulator.strategies import Strategy


@dataclass
class SplitMetrics:
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expected_value: float = 0.0
    max_drawdown: float = 0.0

    @classmethod
    def from_stats(cls, stats: SimulationStats) -> SplitMetrics:
        return cls(
            trades=stats.trades,
            win_rate=stats.win_rate,
            profit_factor=stats.profit_factor,
            expected_value=stats.expected_value,
            max_drawdown=stats.max_drawdown,
        )

    def to_dict(self) -> dict:
        return {
            "trades": self.trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expected_value": self.expected_value,
            "max_drawdown": self.max_drawdown,
        }


@dataclass
class StabilityMetrics:
    ev_retention_validation_train: float | None = None
    ev_retention_test_train: float | None = None
    pf_retention_validation: float | None = None
    pf_retention_test: float | None = None
    family_size: int = 1
    spread_range: str = ""

    def to_dict(self) -> dict:
        return {
            "ev_retention_validation_train": self.ev_retention_validation_train,
            "ev_retention_test_train": self.ev_retention_test_train,
            "pf_retention_validation": self.pf_retention_validation,
            "pf_retention_test": self.pf_retention_test,
            "family_size": self.family_size,
            "spread_range": self.spread_range,
        }


@dataclass
class WalkForwardResult:
    strategy: Strategy
    family: StrategyFamily | None
    train: SplitMetrics
    validation: SplitMetrics
    test: SplitMetrics
    stability: StabilityMetrics
    bootstrap: BootstrapResult
    cost_metrics: dict[str, SplitMetrics] = field(default_factory=dict)
    cost_metrics_same_set: dict[str, SplitMetrics] = field(default_factory=dict)
    cost_trade_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    qualified: bool = False
    reject_reasons: list[str] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return self.strategy.fingerprint()


def _retention(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _load_paths(
    conn: sqlite3.Connection,
    slugs: list[str],
    *,
    min_obs: int,
) -> dict[str, list[dict]]:
    paths: dict[str, list[dict]] = {}
    for slug in slugs:
        path = load_market_path(conn, slug)
        if len(path) >= min_obs:
            paths[slug] = path
    return paths


def _qualify_finalist(
    result: WalkForwardResult,
    *,
    min_test_trades: int,
    min_prob_ev_positive: float,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if result.test.trades < min_test_trades:
        reasons.append(f"test trades {result.test.trades} < {min_test_trades}")
    if result.bootstrap.ev_ci_low <= 0:
        reasons.append(f"EV 95% CI lower bound {result.bootstrap.ev_ci_low:.4f} <= 0")
    if result.bootstrap.prob_ev_positive < min_prob_ev_positive:
        reasons.append(
            f"P(EV>0)={result.bootstrap.prob_ev_positive:.2%} < {min_prob_ev_positive:.0%}"
        )
    return not reasons, reasons


def run_walk_forward(
    conn: sqlite3.Connection,
    *,
    min_obs: int | None = None,
    max_markets: int | None = None,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    test_ratio: float = 0.20,
    top_n: int = 20,
    min_trades: int | None = None,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    min_test_trades: int = MIN_FINALIST_TEST_TRADES,
    min_prob_ev_positive: float = BOOTSTRAP_MIN_PROB_EV_POSITIVE,
    show_progress: bool = True,
) -> tuple[MarketSplit, list[WalkForwardResult]]:
    floor_obs = min_obs or MIN_OBS_PER_MARKET
    slugs = list_markets(conn, min_obs=floor_obs)
    if max_markets:
        slugs = slugs[:max_markets]

    all_paths = _load_paths(conn, slugs, min_obs=floor_obs)
    split = split_markets_chronological(
        all_paths,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        test_ratio=test_ratio,
    )

    train_paths = {s: all_paths[s] for s in split.train if s in all_paths}
    val_paths = {s: all_paths[s] for s in split.validation if s in all_paths}
    test_paths = {s: all_paths[s] for s in split.test if s in all_paths}

    all_contexts = build_contexts(all_paths)
    train_contexts = {s: all_contexts[s] for s in train_paths}
    val_contexts = {s: all_contexts[s] for s in val_paths}
    test_contexts = {s: all_contexts[s] for s in test_paths}

    strategies = generate_discovery_grid()
    train_reps, train_trades, families = discover_on_paths(
        train_paths,
        strategies,
        contexts=train_contexts,
        min_trades=min_trades,
        top_n=top_n,
        dedupe=True,
        show_progress=show_progress,
    )

    family_by_fp = {f.representative.fingerprint: f for f in families}
    selected = [s.strategy for s in train_reps]

    val_trades = evaluate_strategies_on_paths(val_paths, selected, contexts=val_contexts)
    test_trades = evaluate_strategies_on_paths(test_paths, selected, contexts=test_contexts)

    results: list[WalkForwardResult] = []
    for train_stats in train_reps:
        fp = train_stats.fingerprint
        strategy = train_stats.strategy
        family = family_by_fp.get(fp)

        val_stats = compute_stats(strategy, val_trades.get(fp, []))
        test_stats = compute_stats(strategy, test_trades.get(fp, []))
        test_trade_list = test_trades.get(fp, [])

        bootstrap = bootstrap_market_metrics(
            test_trade_list,
            n_samples=bootstrap_samples,
            seed=bootstrap_seed,
        )

        stability = StabilityMetrics(
            ev_retention_validation_train=_retention(
                val_stats.expected_value, train_stats.expected_value,
            ),
            ev_retention_test_train=_retention(
                test_stats.expected_value, train_stats.expected_value,
            ),
            pf_retention_validation=_retention(
                val_stats.profit_factor, train_stats.profit_factor,
            ),
            pf_retention_test=_retention(
                test_stats.profit_factor, train_stats.profit_factor,
            ),
            family_size=family.family_size if family else 1,
            spread_range=family.spread_range if family else f"{strategy.max_spread * 100:.0f}c",
        )

        cost_metrics: dict[str, SplitMetrics] = {}
        cost_metrics_same_set: dict[str, SplitMetrics] = {}
        cost_trade_counts: dict[str, dict[str, int]] = {}
        for scenario in COST_SCENARIOS:
            same_trades = apply_costs_same_trade_set(test_trade_list, scenario)
            filtered = apply_cost_model_filtered(test_trade_list, scenario, seed=bootstrap_seed)
            cost_metrics_same_set[scenario.name] = SplitMetrics.from_stats(
                compute_stats(strategy, same_trades),
            )
            cost_metrics[scenario.name] = SplitMetrics.from_stats(
                compute_stats(strategy, filtered),
            )
            cost_trade_counts[scenario.name] = {
                "in": len(test_trade_list),
                "same_set": len(same_trades),
                "filtered": len(filtered),
            }

        wf = WalkForwardResult(
            strategy=strategy,
            family=family,
            train=SplitMetrics.from_stats(train_stats),
            validation=SplitMetrics.from_stats(val_stats),
            test=SplitMetrics.from_stats(test_stats),
            stability=stability,
            bootstrap=bootstrap,
            cost_metrics=cost_metrics,
            cost_metrics_same_set=cost_metrics_same_set,
            cost_trade_counts=cost_trade_counts,
        )
        qualified, reasons = _qualify_finalist(
            wf,
            min_test_trades=min_test_trades,
            min_prob_ev_positive=min_prob_ev_positive,
        )
        wf.qualified = qualified
        wf.reject_reasons = reasons
        results.append(wf)

    return split, results
