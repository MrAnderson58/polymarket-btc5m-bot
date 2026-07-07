"""Strategy Discovery v2 — leakage-safe train/validation/test pipeline."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import sqlite3

from bot.research.market_behavior.observations import list_markets, load_market_path
from bot.research.strategy_simulator.archetype_context import build_archetype_contexts
from bot.research.strategy_simulator.archetype_simulator import simulate_archetype_on_context
from bot.research.strategy_simulator.archetype_statistics import ArchetypeStats, compute_archetype_stats
from bot.research.strategy_simulator.archetypes import ArchetypeStrategy
from bot.research.strategy_simulator.bootstrap import BootstrapResult, bootstrap_market_metrics
from bot.research.strategy_simulator.config import (
    BOOTSTRAP_MIN_PROB_EV_POSITIVE,
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    MIN_OBS_PER_MARKET,
)
from bot.research.strategy_simulator.cost_model import (
    COST_SCENARIOS,
    apply_cost_model_filtered,
    apply_costs_same_trade_set,
)
from bot.research.strategy_simulator.grid_v2 import generate_discovery_v2_grid, grid_exit_specs
from bot.research.strategy_simulator.market_filter import MarketFilter, list_filtered_market_paths
from bot.research.strategy_simulator.progress import DiscoveryProgress
from bot.research.strategy_simulator.regime_v2 import RegimeComparison, compare_regime_splits
from bot.research.strategy_simulator.rolling_oos import ArchetypeRollingSummary, run_archetype_rolling_oos
from bot.research.strategy_simulator.simulator import VirtualTrade
from bot.research.strategy_simulator.splits import MarketSplit, split_markets_chronological


MIN_TRAIN_TRADES_V2 = 30
MIN_VAL_TRADES_V2 = 10
MAX_PER_FAMILY_V2 = 3
MAX_NEAR_DUPLICATE_TRADE_OVERLAP = 0.95


@dataclass
class DiscoverV2Result:
    strategy: ArchetypeStrategy
    train: ArchetypeStats
    validation: ArchetypeStats
    test: ArchetypeStats
    bootstrap: BootstrapResult
    cost_base_ev: float = 0.0
    cost_stress_ev: float = 0.0
    rolling: ArchetypeRollingSummary | None = None
    validation_passed: bool = False
    reject_reasons: list[str] = field(default_factory=list)
    train_rank: int = 0


@dataclass
class DiscoverV2Report:
    split: MarketSplit
    grid_total: int
    train_candidates: int
    validation_gated: int
    results: list[DiscoverV2Result]
    regime: RegimeComparison
    grid_audit: dict


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


def _trade_set(trades: list[VirtualTrade]) -> frozenset[tuple[str, int]]:
    return frozenset((t.market_slug, t.entry_ts) for t in trades)


def _trade_overlap(a: frozenset[tuple[str, int]], b: frozenset[tuple[str, int]]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _discover_on_paths(
    paths: dict[str, list[dict]],
    strategies: list[ArchetypeStrategy],
    *,
    contexts=None,
    min_trades: int,
    show_progress: bool = False,
) -> tuple[list[ArchetypeStats], dict[str, list[VirtualTrade]]]:
    exit_specs = grid_exit_specs()
    ctx_map = contexts or build_archetype_contexts(paths, exit_specs=exit_specs)
    trades_by_fp: dict[str, list[VirtualTrade]] = defaultdict(list)
    progress = DiscoveryProgress(
        total_markets=len(paths),
        total_strategies=len(strategies),
    ) if show_progress else None

    for mi, slug in enumerate(paths, start=1):
        ctx = ctx_map[slug]
        if progress:
            progress.set_market(mi)
        for si, strategy in enumerate(strategies, start=1):
            trades_by_fp[strategy.fingerprint()].extend(
                simulate_archetype_on_context(ctx, strategy, one_trade_per_market=True),
            )
            if progress:
                progress.tick_strategy(si)
    if progress:
        progress.finish()

    results: list[ArchetypeStats] = []
    for strategy in strategies:
        trades = trades_by_fp[strategy.fingerprint()]
        stats = compute_archetype_stats(strategy, trades)
        if stats.trades >= min_trades:
            results.append(stats)

    ranked = sorted(
        results,
        key=lambda s: (s.expected_value, s.profit_factor, s.win_rate, s.trades),
        reverse=True,
    )
    return ranked, trades_by_fp


def _evaluate_on_paths(
    paths: dict[str, list[dict]],
    strategies: list[ArchetypeStrategy],
    *,
    contexts=None,
) -> dict[str, list[VirtualTrade]]:
    exit_specs = grid_exit_specs()
    ctx_map = contexts or build_archetype_contexts(paths, exit_specs=exit_specs)
    trades_by_fp: dict[str, list[VirtualTrade]] = defaultdict(list)
    for slug in paths:
        ctx = ctx_map[slug]
        for strategy in strategies:
            trades_by_fp[strategy.fingerprint()].extend(
                simulate_archetype_on_context(ctx, strategy, one_trade_per_market=True),
            )
    return trades_by_fp


def select_diverse_candidates(
    ranked: list[ArchetypeStats],
    trades_by_fp: dict[str, list[VirtualTrade]],
    *,
    top_n: int,
    max_per_family: int = MAX_PER_FAMILY_V2,
) -> list[ArchetypeStats]:
    """Cap near-duplicates and enforce family-level diversity."""
    selected: list[ArchetypeStats] = []
    family_counts: dict[str, int] = defaultdict(int)
    selected_sets: list[frozenset[tuple[str, int]]] = []

    for stats in ranked:
        if len(selected) >= top_n:
            break
        fp = stats.fingerprint
        trade_set = _trade_set(trades_by_fp.get(fp, []))
        fam = stats.strategy.family_key
        if family_counts[fam] >= max_per_family:
            continue
        is_dup = any(
            _trade_overlap(trade_set, prev) >= MAX_NEAR_DUPLICATE_TRADE_OVERLAP
            for prev in selected_sets
        )
        if is_dup:
            continue
        selected.append(stats)
        family_counts[fam] += 1
        selected_sets.append(trade_set)
    return selected


def gate_on_validation(
    candidates: list[ArchetypeStats],
    val_trades: dict[str, list[VirtualTrade]],
    *,
    min_val_trades: int = MIN_VAL_TRADES_V2,
    min_val_ev: float = 0.0,
) -> tuple[list[ArchetypeStats], list[str]]:
    """Validation gating — rank order preserved from TRAIN only."""
    passed: list[ArchetypeStats] = []
    reasons: dict[str, list[str]] = {}
    for stats in candidates:
        fp = stats.fingerprint
        val = compute_archetype_stats(stats.strategy, val_trades.get(fp, []))
        rej: list[str] = []
        if val.trades < min_val_trades:
            rej.append(f"val trades {val.trades} < {min_val_trades}")
        if val.expected_value < min_val_ev:
            rej.append(f"val EV {val.expected_value:.4f} < {min_val_ev}")
        reasons[fp] = rej
        if not rej:
            passed.append(stats)
    return passed, [f"{fp}: {', '.join(r)}" for fp, r in reasons.items() if r]


def audit_v1_grid_collapse() -> dict:
    """Explain why v1 grid collapses to one YES family."""
    from bot.research.strategy_simulator.grid import generate_discovery_grid

    grid = generate_discovery_grid()
    yes = [s for s in grid if s.direction == "YES"]
    no = [s for s in grid if s.direction == "NO"]
    yes_cheap = [s for s in yes if s.max_entry <= 0.20 and (s.min_delta or 0) >= 0]
    return {
        "v1_total": len(grid),
        "v1_yes": len(yes),
        "v1_no": len(no),
        "v1_yes_cheap_positive_delta": len(yes_cheap),
        "collapse_causes": [
            "Single hypothesis: cheap ask + positive btc_delta + early window (YES momentum proxy)",
            "Grid uses only btc_delta, spread, seconds_left — no velocity, reversal, or late-window axes",
            "Dedup by identical trade sets collapses spread/tp variants into one family",
            "one_trade_per_market + first-match favors earliest cheap-YES entry in rising BTC windows",
            "TP=0.55 dominates top-20 — exit model not varied in v1 grid",
            "Post-normalization bid/ask: spread filter and TP hits behave differently than pre-fix",
        ],
        "v1_dimensions": {
            "direction": 2,
            "max_entry": 7,
            "min_delta": 6,
            "max_delta": 6,
            "max_spread": 4,
            "min_seconds_left": 5,
            "tp": 4,
            "after_filters": len(grid),
        },
    }


def run_discover_v2(
    conn: sqlite3.Connection,
    *,
    min_obs: int | None = None,
    max_markets: int | None = None,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    test_ratio: float = 0.20,
    top_n: int = 20,
    min_train_trades: int = MIN_TRAIN_TRADES_V2,
    min_val_trades: int = MIN_VAL_TRADES_V2,
    max_per_family: int = MAX_PER_FAMILY_V2,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    rolling_folds: int = 5,
    show_progress: bool = True,
    market_filter: MarketFilter | None = None,
    include_legacy: bool = True,
    archetypes: tuple[str, ...] | None = None,
) -> DiscoverV2Report:
    floor_obs = min_obs or MIN_OBS_PER_MARKET
    if market_filter is not None:
        all_paths = list_filtered_market_paths(conn, market_filter, base_min_obs=floor_obs)
        if max_markets:
            all_paths = dict(list(all_paths.items())[:max_markets])
    else:
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

    exit_specs = grid_exit_specs()
    all_contexts = build_archetype_contexts(all_paths, exit_specs=exit_specs)
    train_ctx = {s: all_contexts[s] for s in train_paths}
    val_ctx = {s: all_contexts[s] for s in val_paths}
    test_ctx = {s: all_contexts[s] for s in test_paths}

    strategies = generate_discovery_v2_grid(
        include_legacy=include_legacy,
        archetypes=archetypes,
    )
    train_ranked, train_trades = _discover_on_paths(
        train_paths,
        strategies,
        contexts=train_ctx,
        min_trades=min_train_trades,
        show_progress=show_progress,
    )
    candidates = select_diverse_candidates(
        train_ranked,
        train_trades,
        top_n=top_n * 3,
        max_per_family=max_per_family,
    )
    val_trades = _evaluate_on_paths(val_paths, [c.strategy for c in candidates], contexts=val_ctx)
    gated, _ = gate_on_validation(
        candidates,
        val_trades,
        min_val_trades=min_val_trades,
    )
    final_candidates = gated[:top_n]

    test_trades = _evaluate_on_paths(
        test_paths,
        [c.strategy for c in final_candidates],
        contexts=test_ctx,
    )

    results: list[DiscoverV2Result] = []
    for rank, train_stats in enumerate(final_candidates, start=1):
        fp = train_stats.fingerprint
        strategy = train_stats.strategy
        val_stats = compute_archetype_stats(strategy, val_trades.get(fp, []))
        test_trade_list = test_trades.get(fp, [])
        test_stats = compute_archetype_stats(strategy, test_trade_list)
        bootstrap = bootstrap_market_metrics(
            test_trade_list,
            n_samples=bootstrap_samples,
            seed=bootstrap_seed,
        )
        base_trades = apply_costs_same_trade_set(test_trade_list, COST_SCENARIOS[1])
        stress_trades = apply_cost_model_filtered(
            test_trade_list, COST_SCENARIOS[2], seed=bootstrap_seed,
        )
        base_ev = compute_archetype_stats(strategy, base_trades).expected_value
        stress_ev = compute_archetype_stats(strategy, stress_trades).expected_value

        rolling = run_archetype_rolling_oos(
            all_paths,
            [strategy],
            n_folds=rolling_folds,
            contexts=all_contexts,
        )
        rolling_summary = rolling[0] if rolling else None

        val_passed = val_stats.trades >= min_val_trades and val_stats.expected_value >= 0
        reject: list[str] = []
        if test_stats.trades < 10:
            reject.append(f"test trades {test_stats.trades} < 10")
        if bootstrap.ev_ci_low <= 0:
            reject.append(f"test EV CI low {bootstrap.ev_ci_low:.4f} <= 0")
        if bootstrap.prob_ev_positive < BOOTSTRAP_MIN_PROB_EV_POSITIVE:
            reject.append(f"P(EV>0)={bootstrap.prob_ev_positive:.2%} < {BOOTSTRAP_MIN_PROB_EV_POSITIVE:.0%}")

        results.append(DiscoverV2Result(
            strategy=strategy,
            train=train_stats,
            validation=val_stats,
            test=test_stats,
            bootstrap=bootstrap,
            cost_base_ev=base_ev,
            cost_stress_ev=stress_ev,
            rolling=rolling_summary,
            validation_passed=val_passed,
            reject_reasons=reject,
            train_rank=rank,
        ))

    regime = compare_regime_splits(train_paths, val_paths, test_paths)
    return DiscoverV2Report(
        split=split,
        grid_total=len(strategies),
        train_candidates=len(candidates),
        validation_gated=len(gated),
        results=results,
        regime=regime,
        grid_audit=audit_v1_grid_collapse(),
    )
