"""Overfit detection (Report v3 §22)."""

from __future__ import annotations

import statistics
from typing import Any

from bot.report.analytics import trade_pnl


def build_overfit_detector(report: dict[str, Any], closed: list) -> dict[str, Any]:
    pnls = [trade_pnl(t) for t in closed]
    walk = report.get("walk_forward", {})
    monte = report.get("monte_carlo", {})
    optimizer = report.get("parameter_optimizer", {})
    equity = report.get("equity_curve", {})

    reasons: list[str] = []
    score = 0

    if len(pnls) < 100:
        return {
            "level": "LOW",
            "overfit": False,
            "reasons": ["Not enough trades for overfit detection"],
            "recommendation": "KEEP CURRENT SETTINGS — accumulate more data",
        }

    half = len(pnls) // 2
    first_half = pnls[:half]
    second_half = pnls[half:]
    first_avg = statistics.mean(first_half)
    second_avg = statistics.mean(second_half)
    if first_avg > 0 and second_avg < first_avg * 0.7:
        deg = (first_avg - second_avg) / abs(first_avg) * 100
        reasons.append(f"Best parameters work better on first half of history ({deg:.0f}% degradation in 2nd half)")
        score += 2
    elif first_avg < 0 and second_avg < first_avg:
        reasons.append("Strategy degrading in second half of history")
        score += 1

    if len(pnls) >= 100:
        last_100 = pnls[-100:]
        baseline = pnls[:-100][-200:] if len(pnls) > 100 else pnls[:-100]
        if baseline:
            base_avg = statistics.mean(baseline)
            last_avg = statistics.mean(last_100)
            if base_avg > 0:
                degrade = (base_avg - last_avg) / abs(base_avg) * 100
                if degrade > 25:
                    reasons.append(f"Last 100 trades degrade by {degrade:.0f}% vs prior window")
                    score += 2

    if walk.get("trend") == "degrading":
        reasons.append("Walk-forward shows degrading trend")
        score += 2

    rolling_pf = [v for v in equity.get("rolling_pf", []) if v is not None and v != float("inf")]
    if len(rolling_pf) >= 5:
        if statistics.mean(rolling_pf[-3:]) < statistics.mean(rolling_pf[:3]) * 0.6:
            reasons.append("Rolling profit factor collapsed on recent trades")
            score += 1

    ruin = float(monte.get("probability_of_ruin", 0) or 0)
    if ruin > 0.15:
        reasons.append(f"Monte Carlo ruin probability elevated ({ruin:.0%})")
        score += 1

    cur = optimizer.get("current", {})
    opt = optimizer.get("optimal", {})
    improvement = optimizer.get("expected_improvement_pct", 0)
    if improvement > 40 and cur.get("trades", 0) < 150:
        reasons.append("Large offline improvement on small sample — likely overfit")
        score += 2

    if opt.get("entry") != cur.get("entry") and improvement > 30:
        reasons.append("Optimal entry differs sharply from live with high claimed improvement")
        score += 1

    if score >= 4:
        level = "HIGH"
        recommendation = "Do not change strategy — overfit risk is high"
    elif score >= 2:
        level = "MEDIUM"
        recommendation = "Proceed with caution — validate on more live trades before changes"
    else:
        level = "LOW"
        recommendation = "No strong overfit signals detected"

    return {
        "level": level,
        "overfit": level in ("MEDIUM", "HIGH"),
        "score": score,
        "reasons": reasons or ["No overfit signals detected"],
        "recommendation": recommendation,
    }
