"""Orchestrate split diagnostics for walk-forward collapse analysis."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from bot.research.market_behavior.observations import list_markets, load_market_path
from bot.research.strategy_simulator.config import MIN_OBS_PER_MARKET
from bot.research.strategy_simulator.cost_model import (
    COST_SCENARIOS,
    CostScenarioResult,
    apply_cost_model_filtered,
    apply_costs_same_trade_set,
)
from bot.research.strategy_simulator.data_quality import SplitDataQuality, analyze_split_data_quality
from bot.research.strategy_simulator.discovery_core import (
    build_contexts,
    discover_on_paths,
    evaluate_strategies_on_paths,
)
from bot.research.strategy_simulator.grid import generate_discovery_grid
from bot.research.strategy_simulator.market_filter import MarketFilter, list_filtered_market_paths
from bot.research.strategy_simulator.opportunity import OpportunityFunnel, analyze_split_opportunity
from bot.research.strategy_simulator.regime import (
    RegimeDiagnostics,
    analyze_regime,
    detect_density_discontinuities,
    observation_density_deciles,
)
from bot.research.strategy_simulator.rolling_oos import RollingOosSummary, run_rolling_oos
from bot.research.strategy_simulator.split_audit import SplitAudit, audit_split
from bot.research.strategy_simulator.splits import MarketSplit, split_markets_chronological
from bot.research.strategy_simulator.statistics import compute_stats
from bot.research.strategy_simulator.strategies import Strategy
from bot.research.strategy_simulator.walk_forward import WalkForwardResult


@dataclass
class StrategyOpportunityReport:
    index: int
    strategy: Strategy
    train: OpportunityFunnel
    validation: OpportunityFunnel
    test: OpportunityFunnel
    train_trades: int = 0
    validation_trades: int = 0
    test_trades: int = 0


@dataclass
class SplitDiagnosticsReport:
    split: MarketSplit
    data_quality: dict[str, SplitDataQuality]
    regime: dict[str, RegimeDiagnostics]
    audits: dict[str, SplitAudit]
    deciles: list[tuple[int, int, float]]
    density_warnings: list[str]
    v4_summary: dict[str, dict]
    strategies: list[StrategyOpportunityReport] = field(default_factory=list)
    cost_checks: list[dict] = field(default_factory=list)
    rolling: list[RollingOosSummary] = field(default_factory=list)
    walk_forward_results: list[WalkForwardResult] = field(default_factory=list)


def _load_paths(conn: sqlite3.Connection, slugs: list[str], *, min_obs: int) -> dict[str, list[dict]]:
    paths: dict[str, list[dict]] = {}
    for slug in slugs:
        path = load_market_path(conn, slug)
        if len(path) >= min_obs:
            paths[slug] = path
    return paths


def _v4_split_summary(paths: dict[str, list[dict]]) -> dict:
    rows = sum(len(p) for p in paths.values())
    markets = len(paths)
    avg = rows / markets if markets else 0.0
    return {"rows": rows, "markets": markets, "avg_rows_per_market": avg}


def run_split_diagnostics(
    conn: sqlite3.Connection,
    *,
    min_obs: int | None = None,
    max_markets: int | None = None,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    test_ratio: float = 0.20,
    top_n: int = 20,
    min_trades: int | None = None,
    n_rolling_folds: int = 5,
    show_progress: bool = False,
    market_filter: MarketFilter | None = None,
) -> SplitDiagnosticsReport:
    floor = min_obs or MIN_OBS_PER_MARKET
    if market_filter is not None:
        all_paths = list_filtered_market_paths(conn, market_filter, base_min_obs=floor)
        if max_markets:
            all_paths = dict(list(all_paths.items())[:max_markets])
    else:
        slugs = list_markets(conn, min_obs=floor)
        if max_markets:
            slugs = slugs[:max_markets]
        all_paths = _load_paths(conn, slugs, min_obs=floor)
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
    train_ctx = {s: all_contexts[s] for s in train_paths}
    val_ctx = {s: all_contexts[s] for s in val_paths}
    test_ctx = {s: all_contexts[s] for s in test_paths}

    deciles = observation_density_deciles(all_paths)
    report = SplitDiagnosticsReport(
        split=split,
        data_quality={
            "TRAIN": analyze_split_data_quality("TRAIN", train_paths),
            "VALIDATION": analyze_split_data_quality("VALIDATION", val_paths),
            "TEST": analyze_split_data_quality("TEST", test_paths),
        },
        regime={
            "TRAIN": analyze_regime("TRAIN", train_paths),
            "VALIDATION": analyze_regime("VALIDATION", val_paths),
            "TEST": analyze_regime("TEST", test_paths),
        },
        audits=audit_split(split, all_paths),
        deciles=deciles,
        density_warnings=detect_density_discontinuities(deciles),
        v4_summary={
            "TRAIN": _v4_split_summary(train_paths),
            "VALIDATION": _v4_split_summary(val_paths),
            "TEST": _v4_split_summary(test_paths),
            "ALL": _v4_split_summary(all_paths),
        },
    )

    train_reps, _, _ = discover_on_paths(
        train_paths,
        generate_discovery_grid(),
        contexts=train_ctx,
        min_trades=min_trades,
        top_n=top_n,
        dedupe=True,
        show_progress=show_progress,
    )
    selected = [s.strategy for s in train_reps]

    val_trades = evaluate_strategies_on_paths(val_paths, selected, contexts=val_ctx)
    test_trades = evaluate_strategies_on_paths(test_paths, selected, contexts=test_ctx)
    train_trades_map = evaluate_strategies_on_paths(train_paths, selected, contexts=train_ctx)

    for i, train_stats in enumerate(train_reps, start=1):
        fp = train_stats.fingerprint
        strategy = train_stats.strategy
        tt = train_trades_map.get(fp, [])
        vt = val_trades.get(fp, [])
        xt = test_trades.get(fp, [])
        report.strategies.append(StrategyOpportunityReport(
            index=i,
            strategy=strategy,
            train=analyze_split_opportunity(train_ctx, strategy, trades=tt),
            validation=analyze_split_opportunity(val_ctx, strategy, trades=vt),
            test=analyze_split_opportunity(test_ctx, strategy, trades=xt),
            train_trades=len(tt),
            validation_trades=len(vt),
            test_trades=len(xt),
        ))

        cost_info: dict = {"strategy_index": i, "fingerprint": fp, "scenarios": []}
        for scenario in COST_SCENARIOS:
            same = apply_costs_same_trade_set(xt, scenario)
            filtered = apply_cost_model_filtered(xt, scenario, seed=42)
            same_stats = compute_stats(strategy, same)
            filt_stats = compute_stats(strategy, filtered)
            cost_info["scenarios"].append({
                "name": scenario.name,
                "same_set": CostScenarioResult(
                    name=scenario.name,
                    trades_in=len(xt),
                    trades_out=len(same),
                    trades_filtered=0,
                    expected_value=same_stats.expected_value,
                    profit_factor=same_stats.profit_factor,
                    same_trade_set=True,
                ),
                "filtered": CostScenarioResult(
                    name=scenario.name,
                    trades_in=len(xt),
                    trades_out=len(filtered),
                    trades_filtered=len(xt) - len(filtered),
                    expected_value=filt_stats.expected_value,
                    profit_factor=filt_stats.profit_factor,
                    same_trade_set=False,
                ),
            })
        report.cost_checks.append(cost_info)

    report.rolling = run_rolling_oos(all_paths, selected, n_folds=n_rolling_folds)
    return report
