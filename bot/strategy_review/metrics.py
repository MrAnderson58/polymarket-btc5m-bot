"""Shared statistical helpers for strategy review."""

from __future__ import annotations

import random
import statistics
from typing import Any

from bot.report.analytics import _metrics

PERMUTATION_ITERATIONS = 1500


def p_value_vs_rest(pnls: list[float], rest: list[float]) -> float:
    if len(pnls) < 3 or len(rest) < 3:
        return 1.0
    try:
        from scipy import stats

        _, p = stats.ttest_ind(pnls, rest, equal_var=False)
        return float(p)
    except Exception:
        observed = abs(statistics.mean(pnls) - statistics.mean(rest))
        combined = pnls + rest
        n_a = len(pnls)
        hits = 0
        for _ in range(PERMUTATION_ITERATIONS):
            random.shuffle(combined)
            diff = abs(statistics.mean(combined[:n_a]) - statistics.mean(combined[n_a:]))
            if diff >= observed:
                hits += 1
        return (hits + 1) / (PERMUTATION_ITERATIONS + 1)


def stability_score(pnls: list[float]) -> float:
    if len(pnls) < 10:
        return 0.0
    chunks = [pnls[i : i + 10] for i in range(0, len(pnls), 10)]
    avgs = [statistics.mean(c) for c in chunks if c]
    if len(avgs) < 2:
        return 40.0
    mean = statistics.mean(avgs)
    cv = statistics.pstdev(avgs) / abs(mean) if mean else 1.0
    return round(max(0.0, min(100.0, 85.0 - cv * 45)), 1)


def overfit_risk(pnls: list[float]) -> str:
    if len(pnls) < 30:
        return "HIGH"
    half = len(pnls) // 2
    first = statistics.mean(pnls[:half])
    second = statistics.mean(pnls[half:])
    if first > 0:
        deg = (first - second) / abs(first) * 100
    else:
        deg = 0.0
    if deg > 35 or (first > 0 and second < 0):
        return "HIGH"
    if deg > 18:
        return "MEDIUM"
    return "LOW"


def walk_forward_pass(pnls: list[float]) -> dict[str, Any]:
    if len(pnls) < 40:
        return {"passed": False, "reason": "insufficient data"}
    split = max(20, len(pnls) // 2)
    train = pnls[:split]
    test = pnls[split:]
    train_avg = statistics.mean(train)
    test_avg = statistics.mean(test)
    passed = test_avg >= train_avg * 0.65 or test_avg > 0
    return {
        "passed": passed,
        "train_avg": round(train_avg, 3),
        "test_avg": round(test_avg, 3),
    }


def confidence_pct(
    *,
    n: int,
    p_value: float,
    stability: float,
    walk_forward_ok: bool,
    overfit: str,
) -> float:
    score = min(40.0, n / 8.0)
    score += max(0.0, 25.0 - p_value * 120)
    score += stability * 0.25
    if walk_forward_ok:
        score += 12
    if overfit == "LOW":
        score += 10
    elif overfit == "MEDIUM":
        score += 4
    return round(min(99.0, max(0.0, score)), 1)


def max_drawdown(pnls: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return round(max_dd, 2)


def enrich_metrics(pnls: list[float], *, rest: list[float] | None = None) -> dict[str, Any]:
    m = _metrics(pnls)
    rest_pnls = rest if rest is not None else []
    p_val = p_value_vs_rest(pnls, rest_pnls) if rest_pnls else 1.0
    stab = stability_score(pnls)
    ovr = overfit_risk(pnls)
    wf = walk_forward_pass(pnls)
    conf = confidence_pct(
        n=m["trades"],
        p_value=p_val,
        stability=stab,
        walk_forward_ok=wf["passed"],
        overfit=ovr,
    )
    ev = m["avg_pnl"] * m["win_rate"] if m["trades"] else 0.0
    return {
        **m,
        "expected_value": round(ev, 3),
        "stability_score": stab,
        "p_value": round(p_val, 4),
        "sample_size": m["trades"],
        "overfit_risk": ovr,
        "walk_forward": wf,
        "confidence_pct": conf,
    }
