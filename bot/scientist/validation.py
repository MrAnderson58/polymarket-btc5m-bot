"""Validation Engine — statistical checks for each hypothesis."""

from __future__ import annotations

import math
import statistics
from typing import Any

from bot.report.analytics import _metrics
from bot.scientist.constants import MIN_SAMPLE_TRADES, QUALITY_RULES
from bot.scientist.dataset import load_enriched_trades


def _p_value_two_sample(a: list[float], b: list[float]) -> float:
    if len(a) < 3 or len(b) < 3:
        return 1.0
    try:
        from scipy import stats

        _, p = stats.ttest_ind(a, b, equal_var=False)
        return float(p)
    except Exception:
        diff = statistics.mean(a) - statistics.mean(b)
        pooled = statistics.pstdev(a + b) or 1.0
        z = abs(diff) / (pooled / math.sqrt(min(len(a), len(b))))
        return max(0.001, min(1.0, math.exp(-0.5 * z * z)))


def _filter_for_hypothesis(rows: list[dict[str, Any]], hypothesis: dict[str, Any]) -> list[dict[str, Any]]:
    htype = hypothesis["hypothesis_type"]
    params = hypothesis.get("params", {})

    if htype == "entry_btc":
        proposed = float(params["proposed_entry"])
        direction = params["btc_direction"]
        return [
            r
            for r in rows
            if abs(float(r.get("entry_price", 0)) - proposed) < 0.006
            and r.get("btc_direction") == direction
        ]

    if htype == "delayed_stop":
        return [r for r in rows if (r.get("exit_reason") or "").upper() == "STOP_LOSS"]

    if htype == "brain_knowledge":
        cond = params.get("condition", "")
        if cond.startswith("regime="):
            regime = cond.split("=", 1)[1]
            return [
                r
                for r in rows
                if (r.get("market_regime") or r.get("regime_label")) == regime
            ]
        if cond.startswith("entry="):
            entry = float(cond.split("=", 1)[1])
            return [r for r in rows if abs(float(r.get("entry_price", 0)) - entry) < 0.006]
        if cond.startswith("btc_"):
            direction = cond.replace("btc_", "")
            return [r for r in rows if r.get("btc_direction") == direction]
        if cond == "spread>0.025":
            return [r for r in rows if (r.get("spread") or 0) > 0.025]
        if cond == "spread<=0.015":
            return [r for r in rows if r.get("spread") is not None and r["spread"] <= 0.015]

    if htype == "trailing_impulse":
        threshold = float(params.get("impulse_threshold", 20))
        return [
            r
            for r in rows
            if (r.get("exit_reason") or "").upper() == "TRAILING_STOP"
            and abs(float(r.get("btc_move_30s") or 0)) >= threshold
        ]

    return rows


def _walk_forward_check(pnls: list[float]) -> dict[str, Any]:
    n = len(pnls)
    if n < 40:
        return {"passed": False, "reason": "insufficient data for walk-forward", "train_avg": 0, "test_avg": 0}
    split = max(20, n // 2)
    train = pnls[:split]
    test = pnls[split:]
    train_avg = statistics.mean(train)
    test_avg = statistics.mean(test)
    passed = test_avg >= train_avg * 0.6 or test_avg > 0
    return {
        "passed": passed,
        "train_size": split,
        "test_size": len(test),
        "train_avg": round(train_avg, 3),
        "test_avg": round(test_avg, 3),
        "reason": "generalizes" if passed else "degrades on hold-out",
    }


def _overfit_check(pnls: list[float], baseline_pnls: list[float]) -> dict[str, Any]:
    if len(pnls) < 30:
        return {"risk": "HIGH", "passed": False, "reason": "sample too small"}
    half = len(pnls) // 2
    first = statistics.mean(pnls[:half])
    second = statistics.mean(pnls[half:])
    degradation = 0.0
    if first > 0:
        degradation = (first - second) / abs(first) * 100
    risk = "LOW"
    if degradation > 40 or (first > 0 and second < 0):
        risk = "HIGH"
    elif degradation > 20:
        risk = "MEDIUM"
    return {
        "risk": risk,
        "passed": risk != "HIGH",
        "degradation_pct": round(degradation, 1),
        "reason": f"second-half degradation {degradation:.0f}%",
    }


def _stability_score(pnls: list[float]) -> dict[str, Any]:
    if len(pnls) < 10:
        return {"score": 0.0, "passed": False, "label": "unstable"}
    chunks = [pnls[i : i + 10] for i in range(0, len(pnls), 10)]
    avgs = [statistics.mean(c) for c in chunks if c]
    if len(avgs) < 2:
        return {"score": 40.0, "passed": False, "label": "medium"}
    mean = statistics.mean(avgs)
    cv = statistics.pstdev(avgs) / abs(mean) if mean else 1.0
    score = max(0.0, min(100.0, 80.0 - cv * 50))
    label = "stable" if score >= 70 else "medium" if score >= 40 else "unstable"
    return {"score": round(score, 1), "passed": score >= 40, "label": label}


def validate_hypothesis(
    conn,
    hypothesis: dict[str, Any],
    *,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = rows if rows is not None else load_enriched_trades(conn)
    baseline_pnls = [float(r.get("pnl") or 0) for r in rows]
    subset_rows = _filter_for_hypothesis(rows, hypothesis)
    subset_pnls = [float(r.get("pnl") or 0) for r in subset_rows]

    baseline_m = _metrics(baseline_pnls)
    subset_m = _metrics(subset_pnls)

    replay_passed = (
        subset_m["trades"] >= 8
        and (
            subset_m["profit_factor"] >= baseline_m["profit_factor"] * 0.95
            or subset_m["avg_pnl"] > baseline_m["avg_pnl"]
        )
    )

    wf = _walk_forward_check(subset_pnls)
    overfit = _overfit_check(subset_pnls, baseline_pnls)
    stability = _stability_score(subset_pnls)
    p_val = _p_value_two_sample(subset_pnls, baseline_pnls)
    sample_ok = subset_m["trades"] >= MIN_SAMPLE_TRADES

    checks = {
        "replay": {
            "passed": replay_passed,
            "subset_trades": subset_m["trades"],
            "subset_pf": round(subset_m["profit_factor"], 3)
            if subset_m["profit_factor"] != float("inf")
            else 99.9,
            "baseline_pf": round(baseline_m["profit_factor"], 3)
            if baseline_m["profit_factor"] != float("inf")
            else 99.9,
            "subset_avg_pnl": round(subset_m["avg_pnl"], 3),
        },
        "walk_forward": wf,
        "overfit": overfit,
        "stability": stability,
        "sample_size": {
            "n": subset_m["trades"],
            "required": MIN_SAMPLE_TRADES,
            "passed": sample_ok,
        },
        "p_value": {
            "value": round(p_val, 4),
            "passed": p_val <= QUALITY_RULES["min_p_value"] or subset_m["avg_pnl"] > baseline_m["avg_pnl"],
        },
    }

    hard_fail = (
        not checks["replay"]["passed"]
        or not checks["overfit"]["passed"]
        or checks["overfit"]["risk"] == "HIGH"
    )
    soft_fail = not wf["passed"] or not sample_ok

    if hard_fail:
        status = "FAILED"
    elif soft_fail:
        status = "REJECTED"
    else:
        status = "PASSED"

    return {
        "status": status,
        "checks": checks,
        "eligible_for_recommendation": _eligible_for_recommendation(checks, status),
    }


def _eligible_for_recommendation(checks: dict[str, Any], status: str) -> bool:
    if status != "PASSED":
        return False
    if checks["sample_size"]["n"] < QUALITY_RULES["min_sample_for_recommendation"]:
        return False
    if QUALITY_RULES["require_walk_forward"] and not checks["walk_forward"]["passed"]:
        return False
    if checks["overfit"]["risk"] == "HIGH":
        return False
    max_risk = QUALITY_RULES["max_overfit_risk"]
    if max_risk == "LOW" and checks["overfit"]["risk"] != "LOW":
        return False
    if max_risk == "MEDIUM" and checks["overfit"]["risk"] == "HIGH":
        return False
    return True
