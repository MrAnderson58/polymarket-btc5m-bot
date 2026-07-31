"""Metrics, bootstrap CI, permutation p-values, FDR correction."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)


def trade_metrics(pnls: Sequence[float]) -> dict[str, Any]:
    xs = [float(p) for p in pnls]
    n = len(xs)
    if n == 0:
        return {
            "n": 0,
            "winrate": None,
            "expectancy": None,
            "pf": None,
            "avg_pnl": None,
            "sharpe": None,
            "total_pnl": 0.0,
        }
    wins = [p for p in xs if p > 0]
    losses = [p for p in xs if p < 0]
    gw, gl = sum(wins), abs(sum(losses))
    if gl > 1e-12:
        pf: float | None = round(gw / gl, 4)
    elif gw > 0:
        pf = None  # inf
    else:
        pf = 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    sharpe = None
    if var > 1e-18:
        sharpe = round(mean / math.sqrt(var), 4)
    elif abs(mean) < 1e-12:
        sharpe = 0.0
    return {
        "n": n,
        "winrate": round(100.0 * len(wins) / n, 2),
        "expectancy": round(mean, 4),
        "pf": pf,
        "pf_inf": pf is None and gw > 0,
        "avg_pnl": round(mean, 4),
        "sharpe": sharpe,
        "total_pnl": round(sum(xs), 4),
    }


def bootstrap_expectancy_ci(
    pnls: Sequence[float],
    *,
    n_boot: int = 300,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float | None, float | None]:
    if len(pnls) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    arr = np.asarray(list(pnls), dtype=float)
    means = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        sample = rng.choice(arr, size=len(arr), replace=True)
        means[i] = float(sample.mean())
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return round(lo, 4), round(hi, 4)


def permutation_pvalue(
    selected: Sequence[float],
    baseline: Sequence[float],
    *,
    n_perm: int = 200,
    seed: int = 0,
) -> float | None:
    """Two-sided p-value vs random subsets of same size drawn from baseline."""
    if len(selected) < 3 or len(baseline) < 5:
        return None
    a = np.asarray(list(selected), dtype=float)
    b = np.asarray(list(baseline), dtype=float)
    n_a = len(a)
    if n_a >= len(b):
        return None
    overall = float(b.mean())
    obs = abs(float(a.mean()) - overall)
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(n_perm):
        idx = rng.choice(len(b), size=n_a, replace=False)
        d = abs(float(b[idx].mean()) - overall)
        if d >= obs - 1e-15:
            extreme += 1
    return round((extreme + 1) / (n_perm + 1), 6)


def benjamini_hochberg(
    pvalues: list[float | None],
    *,
    alpha: float = 0.10,
) -> list[float | None]:
    """Return q-values (FDR-adjusted). None inputs stay None."""
    indexed = [(i, p) for i, p in enumerate(pvalues) if p is not None]
    qvals: list[float | None] = [None] * len(pvalues)
    if not indexed:
        return qvals
    indexed.sort(key=lambda t: t[1])
    m = len(indexed)
    prev = 1.0
    # from largest p to smallest
    adj: dict[int, float] = {}
    for rank_rev, (idx, p) in enumerate(reversed(indexed)):
        rank = m - rank_rev  # 1..m
        val = min(prev, p * m / rank)
        prev = val
        adj[idx] = min(1.0, val)
    for i, q in adj.items():
        qvals[i] = round(q, 6)
    return qvals


def bonferroni(pvalues: list[float | None]) -> list[float | None]:
    m = sum(1 for p in pvalues if p is not None)
    if m == 0:
        return list(pvalues)
    return [None if p is None else round(min(1.0, p * m), 6) for p in pvalues]


def effective_pf(m: dict[str, Any]) -> float:
    if m.get("pf_inf"):
        return 10.0
    if m.get("pf") is None:
        return 0.0
    return float(m["pf"])


def pnl_list(rows: list[dict[str, Any]]) -> list[float]:
    return [float(_safe_float(r.get("pnl")) or 0.0) for r in rows]


__all__ = [
    "benjamini_hochberg",
    "bonferroni",
    "bootstrap_expectancy_ci",
    "effective_pf",
    "permutation_pvalue",
    "pnl_list",
    "trade_metrics",
]
