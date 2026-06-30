"""Final health scores for the analytics report."""

from __future__ import annotations

from typing import Any


def _clamp(score: float) -> int:
    return max(0, min(100, int(round(score))))


def compute_scores(report: dict[str, Any]) -> dict[str, int]:
    positions = report.get("current_positions", {})
    closed_metrics = report.get("overall_performance", {})
    exec_q = report.get("execution_quality", {})
    walk = report.get("walk_forward", {})
    short = report.get("stop_loss_short_recovery", {})

    health = 100
    health -= positions.get("failed_exit_intents", 0) * 15
    health -= positions.get("orphan_intents", 0) * 10
    health -= len(positions.get("warnings", [])) * 20

    strategy = 50.0
    pf = closed_metrics.get("profit_factor", 0)
    wr = closed_metrics.get("win_rate", 0)
    if pf == float("inf"):
        strategy += 30
    elif pf >= 2:
        strategy += 25
    elif pf >= 1.5:
        strategy += 15
    elif pf >= 1:
        strategy += 5
    else:
        strategy -= 15
    strategy += (wr - 0.5) * 40
    if walk.get("trend") == "improving":
        strategy += 10
    elif walk.get("trend") == "degrading":
        strategy -= 15

    execution = 85.0
    if exec_q.get("rejected_intents", 0) > 0:
        execution -= min(30, exec_q["rejected_intents"] * 5)
    if exec_q.get("partial_fills", 0) > 0:
        execution -= min(15, exec_q["partial_fills"] * 3)
    slip = abs(exec_q.get("avg_buy_slippage", 0)) + abs(
        exec_q.get("avg_sell_slippage", 0)
    )
    execution -= min(20, slip * 500)

    risk = 80.0
    risk -= len(positions.get("warnings", [])) * 25
    if positions.get("open_count", 0) > 0:
        risk -= 5

    adaptation = 70.0
    if short.get("recovered_entry_60s_rate", 0) > 0.4:
        adaptation += 10
    btc_filter = report.get("btc_filter_analysis", {})
    if btc_filter.get("recommended_thresholds"):
        adaptation += 5

    data_quality = 90.0
    if report.get("bot_health", {}).get("last_market_check_at") is None:
        data_quality -= 40
    skipped = short.get("skipped_no_checks", 0)
    total = short.get("total_stop_loss", 1)
    data_quality -= (skipped / max(total, 1)) * 30

    overall = (
        health * 0.25
        + strategy * 0.25
        + execution * 0.15
        + risk * 0.2
        + adaptation * 0.1
        + data_quality * 0.05
    )

    return {
        "overall": _clamp(overall),
        "health": _clamp(health),
        "strategy": _clamp(strategy),
        "execution": _clamp(execution),
        "risk": _clamp(risk),
        "market_adaptation": _clamp(adaptation),
        "data_quality": _clamp(data_quality),
    }
