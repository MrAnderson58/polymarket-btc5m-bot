"""Transition scoring: metrics + bootstrap + permutation + walk-forward + READY gate."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
    bootstrap_expectancy_ci,
    permutation_pvalue,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)

MIN_N = 20
MIN_WR = 55.0
MIN_EV = 0.15
MAX_PERM_P = 0.10
MIN_STABILITY = 0.55
MIN_OOS_EV = 0.0


def score_pnls(
    pnls: Sequence[float],
    *,
    baseline: Sequence[float] | None = None,
    n_boot: int = 200,
    n_perm: int = 150,
    seed: int = 11,
) -> dict[str, Any]:
    xs = [float(p) for p in pnls]
    perf = cluster_performance(xs)
    ci_lo, ci_hi = bootstrap_expectancy_ci(xs, n_boot=n_boot, seed=seed)

    perm_p: float | None = None
    if baseline is not None and len(baseline) > len(xs) >= 3:
        perm_p = permutation_pvalue(xs, baseline, n_perm=n_perm, seed=seed)
    elif len(xs) >= 5:
        # Sign-flip null (one-sample) when no larger baseline corpus.
        rng = np.random.default_rng(seed)
        arr = np.asarray(xs, dtype=float)
        obs = abs(float(arr.mean()))
        extreme = 0
        for _ in range(n_perm):
            signs = rng.choice(np.array([-1.0, 1.0]), size=len(arr))
            if abs(float((arr * signs).mean())) >= obs - 1e-15:
                extreme += 1
        perm_p = round((extreme + 1) / (n_perm + 1), 6)

    # Walk-forward / OOS: first 70% train, last 30% test
    oos_wr = oos_ev = None
    temporal_stability = None
    if len(xs) >= MIN_N:
        cut = max(int(len(xs) * 0.7), MIN_N // 2)
        train, test = xs[:cut], xs[cut:]
        if test:
            tr = cluster_performance(train)
            te = cluster_performance(test)
            oos_wr = te.get("wr")
            oos_ev = te.get("ev")
            # stability: sign(EV) agreement + relative EV closeness
            tr_ev = float(tr.get("ev") or 0)
            te_ev = float(te.get("ev") or 0)
            same_sign = (tr_ev >= 0 and te_ev >= 0) or (tr_ev < 0 and te_ev < 0)
            denom = max(abs(tr_ev), abs(te_ev), 1e-6)
            closeness = 1.0 - min(1.0, abs(tr_ev - te_ev) / denom)
            temporal_stability = round((0.6 if same_sign else 0.2) + 0.4 * closeness, 4)

    ready = bool(
        int(perf.get("n") or 0) >= MIN_N
        and float(perf.get("wr") or 0) >= MIN_WR
        and float(perf.get("ev") or 0) >= MIN_EV
        and (perm_p is None or float(perm_p) <= MAX_PERM_P)
        and (ci_lo is None or float(ci_lo) > 0)
        and (oos_ev is None or float(oos_ev) >= MIN_OOS_EV)
        and (temporal_stability is None or float(temporal_stability) >= MIN_STABILITY)
    )
    score = round(
        float(perf.get("ev") or 0) * 2
        + float(perf.get("wr") or 0) / 100.0
        + float(perf.get("sharpe") or 0)
        - (float(perm_p) if perm_p is not None else 0.5),
        4,
    )
    return {
        "trades": perf.get("n"),
        "n": perf.get("n"),
        "wr": perf.get("wr"),
        "pf": perf.get("pf"),
        "ev": perf.get("ev"),
        "sharpe": perf.get("sharpe"),
        "max_dd": perf.get("max_dd"),
        "ci_lo": ci_lo if ci_lo is not None else perf.get("ci_lo"),
        "ci_hi": ci_hi if ci_hi is not None else perf.get("ci_hi"),
        "perm_p": perm_p,
        "oos_wr": oos_wr,
        "oos_ev": oos_ev,
        "temporal_stability": temporal_stability,
        "ready": ready,
        "score": score,
    }


__all__ = ["score_pnls"]
