"""Explainable trade metrics for Market Mathematics V1."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)


def pnl_array(rows: Sequence[dict[str, Any]]) -> np.ndarray:
    return np.asarray([float(_safe_float(r.get("pnl")) or 0.0) for r in rows], dtype=float)


def rich_metrics(
    pnls: Sequence[float] | np.ndarray,
    *,
    baseline: Sequence[float] | np.ndarray | None = None,
    n_boot: int = 200,
    n_perm: int = 150,
    seed: int = 0,
) -> dict[str, Any]:
    arr = np.asarray(list(pnls), dtype=float) if not isinstance(pnls, np.ndarray) else pnls.astype(float)
    n = int(arr.size)
    empty = {
        "n": 0,
        "winrate": None,
        "profit_factor": None,
        "expectancy": None,
        "mean_pnl": None,
        "median_pnl": None,
        "std": None,
        "ci95": (None, None),
        "sharpe": None,
        "kelly": None,
        "p_value": None,
        "delta_ev": None,
        "pf_inf": False,
    }
    if n == 0:
        return empty
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gw, gl = float(wins.sum()) if wins.size else 0.0, float(np.abs(losses.sum())) if losses.size else 0.0
    pf_inf = False
    if gl > 1e-12:
        pf: float | None = round(gw / gl, 4)
    elif gw > 0:
        pf = None
        pf_inf = True
    else:
        pf = 0.0
    mean = float(arr.mean())
    median = float(np.median(arr))
    std = float(arr.std()) if n > 1 else 0.0
    sharpe = None
    if std > 1e-12:
        sharpe = round(mean / std, 4)
    elif abs(mean) < 1e-12:
        sharpe = 0.0
    # Kelly fraction from winrate / payoff (clipped)
    wr = float(wins.size) / n
    avg_w = float(wins.mean()) if wins.size else 0.0
    avg_l = float(np.abs(losses.mean())) if losses.size else 0.0
    kelly = None
    if avg_l > 1e-12 and avg_w > 0:
        b = avg_w / avg_l
        kelly = round(max(-1.0, min(1.0, wr - (1.0 - wr) / b)), 4)
    elif wr >= 1.0 and avg_w > 0:
        kelly = 1.0

    ci = _bootstrap_ci(arr, n_boot=n_boot, seed=seed)
    p_value = None
    delta_ev = None
    if baseline is not None:
        base = np.asarray(list(baseline), dtype=float) if not isinstance(baseline, np.ndarray) else baseline.astype(float)
        if base.size >= 5 and n >= 3 and n < base.size:
            delta_ev = round(mean - float(base.mean()), 4)
            p_value = _perm_pvalue(arr, base, n_perm=n_perm, seed=seed)
        elif base.size >= 5:
            delta_ev = round(mean - float(base.mean()), 4)

    return {
        "n": n,
        "winrate": round(100.0 * wr, 2),
        "profit_factor": pf if not pf_inf else "inf",
        "expectancy": round(mean, 4),
        "mean_pnl": round(mean, 4),
        "median_pnl": round(median, 4),
        "std": round(std, 4),
        "ci95": ci,
        "sharpe": sharpe,
        "kelly": kelly,
        "p_value": p_value,
        "delta_ev": delta_ev,
        "pf_inf": pf_inf,
    }


def _bootstrap_ci(
    arr: np.ndarray,
    *,
    n_boot: int,
    seed: int,
    alpha: float = 0.05,
) -> tuple[float | None, float | None]:
    if arr.size < 5:
        return None, None
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        means[i] = float(rng.choice(arr, size=arr.size, replace=True).mean())
    return round(float(np.quantile(means, alpha / 2)), 4), round(float(np.quantile(means, 1 - alpha / 2)), 4)


def _perm_pvalue(
    selected: np.ndarray,
    baseline: np.ndarray,
    *,
    n_perm: int,
    seed: int,
) -> float | None:
    n = selected.size
    if n < 3 or n >= baseline.size:
        return None
    overall = float(baseline.mean())
    obs = abs(float(selected.mean()) - overall)
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(n_perm):
        idx = rng.choice(baseline.size, size=n, replace=False)
        if abs(float(baseline[idx].mean()) - overall) >= obs - 1e-15:
            extreme += 1
    return round((extreme + 1) / (n_perm + 1), 6)


def effective_pf(m: dict[str, Any]) -> float:
    if m.get("pf_inf") or m.get("profit_factor") == "inf":
        return 10.0
    if m.get("profit_factor") is None:
        return 0.0
    return float(m["profit_factor"])


def significance_ok(m: dict[str, Any], *, min_n: int = 20, alpha: float = 0.10) -> bool:
    if int(m.get("n") or 0) < min_n:
        return False
    p = m.get("p_value")
    if p is None:
        # allow positive edge with CI above 0
        ci = m.get("ci95") or (None, None)
        return bool(ci[0] is not None and ci[0] > 0 and (m.get("expectancy") or 0) > 0)
    return float(p) <= alpha and float(m.get("expectancy") or 0) > 0


__all__ = [
    "effective_pf",
    "pnl_array",
    "rich_metrics",
    "significance_ok",
]
