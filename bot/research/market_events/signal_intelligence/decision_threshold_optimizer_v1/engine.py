"""Decision Threshold Optimizer V1 orchestrator."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.cache import (
    build_feature_cache,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.evaluate import (
    accept_mask,
    evaluate_thresholds,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.false_rejects import (
    analyze_top_false_rejects,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.grid import (
    BASELINE,
    ThresholdSet,
    grid_size,
    iter_threshold_grid,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.pareto import (
    best_overall,
    pareto_frontier,
    select_profiles,
)
from bot.research.market_events.signal_intelligence.decision_threshold_optimizer_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.context import (
    build_decision_context,
)

BEST_PROFILE_JSON = BASE_DIR / "reports" / "research" / "decision_threshold_optimizer_v1" / "best_thresholds.json"


def run_decision_threshold_optimize(
    conn: Any,
    *,
    limit: int | None = None,
    write_reports: bool = True,
    min_trades_pareto: int = 30,
) -> dict[str, Any]:
    t0 = time.time()
    ctx = build_decision_context(conn, limit=limit)
    if not ctx.get("ok"):
        return {
            "ok": False,
            "error": "empty_context",
            "terminal": "DECISION THRESHOLD OPTIMIZER V1\n\nERROR empty corpus",
            "research_only": True,
        }

    t_cache = time.time()
    cache = build_feature_cache(ctx)
    cache_sec = round(time.time() - t_cache, 3)

    baseline_mask = accept_mask(cache, BASELINE)
    before = evaluate_thresholds(cache, BASELINE)

    t_grid = time.time()
    all_results: list[dict[str, Any]] = []
    for thr in iter_threshold_grid():
        all_results.append(evaluate_thresholds(cache, thr, baseline_mask=baseline_mask))
    grid_sec = round(time.time() - t_grid, 3)

    pareto = pareto_frontier(all_results, min_trades=min_trades_pareto)
    profiles = select_profiles(pareto, all_results)
    best = best_overall(profiles, pareto)

    # False reject analysis under baseline (current problem)
    fr_baseline = analyze_top_false_rejects(cache, BASELINE, top_n=100)
    fr_best = analyze_top_false_rejects(
        cache,
        ThresholdSet(**(best.get("thresholds") or BASELINE.as_dict())),
        top_n=100,
    ) if best.get("thresholds") else {}

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "grid_size": grid_size(),
        "n_evaluated": len(all_results),
        "cache_n": cache.get("n"),
        "cache_sec": cache_sec,
        "grid_sec": grid_sec,
        "before": before,
        "best": best,
        "profiles": profiles,
        "pareto": pareto[:80],
        "false_reject_analysis": fr_baseline,
        "false_reject_analysis_best": fr_best,
        "all_results": all_results,
        "elapsed_sec": elapsed,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "execution_unchanged": True,
        "paper_execution_unchanged": True,
        "optimizer_unchanged": True,
    }
    result["terminal"] = format_terminal(result)

    if write_reports:
        paths = write_artifacts(result)
        BEST_PROFILE_JSON.parent.mkdir(parents=True, exist_ok=True)
        BEST_PROFILE_JSON.write_text(
            json.dumps({
                "thresholds": best.get("thresholds"),
                "label": best.get("label"),
                "metrics": {
                    k: best.get(k)
                    for k in (
                        "trades", "wr", "pf", "ev", "sharpe", "max_dd",
                        "false_rejects", "false_approvals", "precision", "recall", "f1",
                    )
                },
                "before": {
                    k: before.get(k)
                    for k in (
                        "trades", "wr", "pf", "ev", "sharpe", "max_dd",
                        "false_rejects", "false_approvals", "precision", "recall", "f1",
                    )
                },
            }, indent=2, default=str),
            encoding="utf-8",
        )
        paths["best_thresholds_json"] = str(BEST_PROFILE_JSON)
        result["paths"] = paths
    # drop heavy list from return after reports
    result.pop("all_results", None)
    return result


def run_decision_threshold_report(
    conn: Any,
    *,
    write_reports: bool = True,
) -> dict[str, Any]:
    """Load last optimize artifacts or re-run."""
    json_path = BASE_DIR / "reports" / "research" / "decision_threshold_optimizer_v1" / "threshold_optimize.json"
    if json_path.exists():
        data = json.loads(json_path.read_text(encoding="utf-8"))
        data["ok"] = True
        data["research_only"] = True
        data["terminal"] = format_terminal(data)
        if write_reports:
            # refresh md from cached json (without all_results heatmap uses empty)
            data["all_results"] = []
            write_artifacts(data)
            data.pop("all_results", None)
        return data
    return run_decision_threshold_optimize(conn, write_reports=write_reports)


__all__ = ["run_decision_threshold_optimize", "run_decision_threshold_report"]
