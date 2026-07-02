"""Safety gate — blocks unsafe recommendations."""

from __future__ import annotations

from typing import Any

from bot.strategy_review.constants import MIN_TRADES_SINCE_CHANGE, MIN_CONFIDENCE_PCT, MAX_P_VALUE, SAFETY_GATE


def check_safety_gate(
    report: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """
    Returns passed=False if ANY gate fails.
    candidate must include: confidence_pct, p_value, walk_forward.passed, overfit_risk, sample_size
    """
    live = report.get("live_sample", {})
    trades_since = int(live.get("current_since_change", live.get("total_trades", 0)))
    total_trades = int(live.get("total_trades", 0))

    failures: list[str] = []
    if trades_since < MIN_TRADES_SINCE_CHANGE:
        failures.append(
            f"выборка после последнего изменения {trades_since} < {MIN_TRADES_SINCE_CHANGE}"
        )

    wf = candidate.get("walk_forward") or {}
    if SAFETY_GATE["require_walk_forward_pass"] and not wf.get("passed", False):
        failures.append("walk-forward FAIL")

    if candidate.get("overfit_risk") == "HIGH":
        failures.append("overfit HIGH")

    conf = float(candidate.get("confidence_pct", 0))
    if conf < MIN_CONFIDENCE_PCT:
        failures.append(f"confidence {conf:.0f}% < {MIN_CONFIDENCE_PCT:.0f}%")

    p_val = float(candidate.get("p_value", 1.0))
    if p_val > MAX_P_VALUE:
        failures.append(f"p-value {p_val:.3f} > {MAX_P_VALUE}")

    sample = int(candidate.get("sample_size", candidate.get("trades", 0)))
    if sample < 30:
        failures.append("insufficient per-parameter sample")

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "trades_since_last_change": trades_since,
        "total_trades": total_trades,
        "next_review_after_trades": max(0, MIN_TRADES_SINCE_CHANGE - trades_since),
        "rules": SAFETY_GATE,
    }
