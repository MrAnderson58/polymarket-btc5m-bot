"""Statistical validation for Edge Discovery V3."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
    benjamini_hochberg,
    bootstrap_expectancy_ci,
    effective_pf,
    permutation_pvalue,
    trade_metrics,
)


def max_drawdown(pnls: Sequence[float] | np.ndarray) -> float | None:
    if pnls is None or len(pnls) == 0:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += float(p)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(float(max_dd), 4)


def sortino_ratio(pnls: Sequence[float] | np.ndarray) -> float | None:
    arr = np.asarray(list(pnls), dtype=float)
    if len(arr) < 3:
        return None
    mean = float(arr.mean())
    downside = arr[arr < 0.0]
    if len(downside) == 0:
        return 99.0 if mean > 0 else 0.0
    dd = float(np.sqrt(np.mean(downside ** 2)))
    if dd < 1e-12:
        return 99.0 if mean > 0 else 0.0
    return round(mean / dd, 4)


def bayesian_edge_posterior(
    pnls: Sequence[float] | np.ndarray,
    *,
    n_boot: int = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """Bootstrap posterior proxy: P(EV > 0) and mean/CI of expectancy."""
    arr = np.asarray(list(pnls), dtype=float)
    if len(arr) < 5:
        return {
            "posterior_mean": None,
            "prob_edge_gt_0": None,
            "posterior_ci95": (None, None),
        }
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        means[i] = float(rng.choice(arr, size=len(arr), replace=True).mean())
    return {
        "posterior_mean": round(float(means.mean()), 4),
        "prob_edge_gt_0": round(float(np.mean(means > 0.0)), 4),
        "posterior_ci95": (
            round(float(np.quantile(means, 0.025)), 4),
            round(float(np.quantile(means, 0.975)), 4),
        ),
    }


def walk_forward_ok(
    pnls: np.ndarray,
    mask: np.ndarray,
    closed: np.ndarray,
    *,
    n_folds: int = 4,
) -> dict[str, Any]:
    idx = np.where(mask)[0]
    if len(idx) < max(8, n_folds * 2):
        return {"wf_ok": False, "wf_pos_frac": None, "wf_mean_ev": None}
    order = idx[np.argsort(closed[idx])]
    chunk = max(1, len(order) // n_folds)
    evs: list[float] = []
    for f in range(n_folds):
        lo = f * chunk
        hi = len(order) if f == n_folds - 1 else (f + 1) * chunk
        part = order[lo:hi]
        if len(part) < 3:
            continue
        evs.append(float(pnls[part].mean()))
    if not evs:
        return {"wf_ok": False, "wf_pos_frac": None, "wf_mean_ev": None}
    frac = sum(1 for e in evs if e > 0) / len(evs)
    mean_ev = float(np.mean(evs))
    return {
        "wf_ok": bool(frac >= 0.5 and mean_ev > 0),
        "wf_pos_frac": round(frac, 4),
        "wf_mean_ev": round(mean_ev, 4),
    }


def rolling_window_ok(
    pnls: np.ndarray,
    mask: np.ndarray,
    closed: np.ndarray,
    *,
    n_windows: int = 5,
) -> dict[str, Any]:
    idx = np.where(mask)[0]
    if len(idx) < n_windows * 2:
        return {"rolling_ok": False, "rolling_pos_frac": None}
    order = idx[np.argsort(closed[idx])]
    chunk = max(1, len(order) // n_windows)
    pos = 0
    used = 0
    for w in range(n_windows):
        lo = w * chunk
        hi = len(order) if w == n_windows - 1 else (w + 1) * chunk
        part = order[lo:hi]
        if len(part) < 2:
            continue
        used += 1
        if float(pnls[part].mean()) > 0:
            pos += 1
    if used == 0:
        return {"rolling_ok": False, "rolling_pos_frac": None}
    frac = pos / used
    return {"rolling_ok": bool(frac >= 0.6), "rolling_pos_frac": round(frac, 4)}


def expanding_window_ok(
    pnls: np.ndarray,
    mask: np.ndarray,
    closed: np.ndarray,
    *,
    n_cuts: int = 4,
) -> dict[str, Any]:
    idx = np.where(mask)[0]
    if len(idx) < n_cuts * 3:
        return {"expanding_ok": False, "expanding_pos_frac": None}
    order = idx[np.argsort(closed[idx])]
    pos = 0
    used = 0
    for c in range(1, n_cuts + 1):
        hi = max(3, int(len(order) * c / n_cuts))
        part = order[:hi]
        used += 1
        if float(pnls[part].mean()) > 0:
            pos += 1
    frac = pos / max(1, used)
    return {"expanding_ok": bool(frac >= 0.75), "expanding_pos_frac": round(frac, 4)}


def regime_slice_ok(
    pnls: np.ndarray,
    mask: np.ndarray,
    numeric: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Bull/bear/range + high/low vol via trend & atr_pct quantiles (data-driven)."""
    trend = numeric.get("trend")
    atr = numeric.get("atr_pct")
    if trend is None or atr is None:
        return {"regime_ok": True, "regime_tests": {}}
    valid_t = np.isfinite(trend)
    valid_a = np.isfinite(atr)
    tests: dict[str, bool] = {}
    slices = {}
    if valid_t.sum() >= 10:
        q33, q66 = np.nanquantile(trend[valid_t], [0.33, 0.66])
        slices["bear"] = mask & valid_t & (trend <= q33)
        slices["range"] = mask & valid_t & (trend > q33) & (trend < q66)
        slices["bull"] = mask & valid_t & (trend >= q66)
    if valid_a.sum() >= 10:
        med = float(np.nanmedian(atr[valid_a]))
        slices["low_vol"] = mask & valid_a & (atr <= med)
        slices["high_vol"] = mask & valid_a & (atr > med)
    for name, m in slices.items():
        n = int(m.sum())
        if n < 5:
            tests[name] = True  # insufficient — do not fail
            continue
        tests[name] = bool(float(pnls[m].mean()) > -0.05)  # soft: not strongly negative
    ok = all(tests.values()) if tests else True
    return {"regime_ok": ok, "regime_tests": tests}


def oos_holdout_ok(
    pnls: np.ndarray,
    mask: np.ndarray,
    closed: np.ndarray,
    *,
    holdout_frac: float = 0.30,
) -> dict[str, Any]:
    order = np.argsort(closed)
    n = len(order)
    if n < 10:
        return {"oos_ok": False, "oos_n": 0, "oos_ev": None}
    cut = int(n * (1.0 - holdout_frac))
    oos = np.zeros(n, dtype=bool)
    oos[order[cut:]] = True
    sel = mask & oos
    n_sel = int(sel.sum())
    if n_sel < 3:
        return {"oos_ok": False, "oos_n": n_sel, "oos_ev": None}
    ev = float(pnls[sel].mean())
    base = float(pnls[oos].mean())
    return {
        "oos_ok": bool(ev > base and ev > 0),
        "oos_n": n_sel,
        "oos_ev": round(ev, 4),
        "oos_base_ev": round(base, 4),
    }


def temporal_stability(
    pnls: np.ndarray,
    mask: np.ndarray,
    closed: np.ndarray,
    *,
    n_buckets: int = 5,
) -> dict[str, Any]:
    idx = np.where(mask)[0]
    if len(idx) < n_buckets * 2:
        return {"stable": False, "stability": 0.0, "cv": None}
    order = idx[np.argsort(closed[idx])]
    chunk = max(1, len(order) // n_buckets)
    evs: list[float] = []
    for b in range(n_buckets):
        lo = b * chunk
        hi = len(order) if b == n_buckets - 1 else (b + 1) * chunk
        part = order[lo:hi]
        if len(part) < 2:
            continue
        evs.append(float(pnls[part].mean()))
    if len(evs) < 2:
        return {"stable": False, "stability": 0.0, "cv": None}
    mean = float(np.mean(evs))
    std = float(np.std(evs))
    cv = abs(std / mean) if abs(mean) > 1e-9 else 99.0
    pos_frac = sum(1 for e in evs if e > 0) / len(evs)
    stability = max(0.0, min(1.0, pos_frac * (1.0 / (1.0 + cv))))
    return {
        "stable": bool(pos_frac >= 0.6 and cv < 2.5),
        "stability": round(stability, 4),
        "cv": round(cv, 4),
        "pos_frac": round(pos_frac, 4),
    }


def quality_score(c: dict[str, Any]) -> float:
    pf = effective_pf(c) if c.get("pf") is not None or c.get("pf_inf") else 0.0
    if c.get("pf_inf"):
        pf = 10.0
    ev = float(c.get("expectancy") or 0.0)
    wr = float(c.get("winrate") or 0.0) / 100.0
    n = int(c.get("n") or 0)
    stab = float(c.get("stability") or 0.0)
    p = c.get("p_value")
    robust = 1.0 - float(p) if p is not None else 0.5
    posterior = float(c.get("prob_edge_gt_0") or 0.5)
    oos = 1.0 if c.get("oos_ok") else 0.0
    wf = 1.0 if c.get("wf_ok") else 0.0
    regime = 1.0 if c.get("regime_ok") else 0.0

    def clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    score = 100.0 * (
        0.14 * clamp01(pf / 2.5)
        + 0.14 * clamp01((ev + 1.0) / 3.0)
        + 0.08 * clamp01(wr)
        + 0.12 * clamp01(stab)
        + 0.10 * clamp01(n / 400.0)
        + 0.10 * clamp01(robust)
        + 0.12 * clamp01(posterior)
        + 0.08 * oos
        + 0.06 * wf
        + 0.06 * regime
    )
    return round(float(score), 2)


def cheap_screen(pnls: np.ndarray, mask: np.ndarray) -> dict[str, Any] | None:
    """Fast EV/n/WR/PF for combinatorial screening."""
    n = int(mask.sum())
    if n == 0:
        return None
    sel = pnls[mask]
    mean = float(sel.mean())
    wins = sel[sel > 0]
    losses = sel[sel < 0]
    gw, gl = float(wins.sum()) if len(wins) else 0.0, float(abs(losses.sum())) if len(losses) else 0.0
    if gl > 1e-12:
        pf: float | None = gw / gl
    elif gw > 0:
        pf = None
    else:
        pf = 0.0
    wr = 100.0 * float(np.mean(sel > 0))
    return {"n": n, "expectancy": mean, "pf": pf, "winrate": wr, "pf_inf": pf is None and gw > 0}


def full_validate(
    pnls: np.ndarray,
    mask: np.ndarray,
    closed: np.ndarray,
    numeric: dict[str, np.ndarray],
    *,
    seed: int = 0,
    n_boot: int = 100,
    n_perm: int = 60,
) -> dict[str, Any]:
    sel = pnls[mask]
    m = trade_metrics(sel.tolist())
    ci = bootstrap_expectancy_ci(sel.tolist(), n_boot=n_boot, seed=seed)
    p = permutation_pvalue(sel.tolist(), pnls.tolist(), n_perm=n_perm, seed=seed + 7)
    bayes = bayesian_edge_posterior(sel, n_boot=n_boot, seed=seed + 3)
    out: dict[str, Any] = {
        **m,
        "max_dd": max_drawdown(sel),
        "sortino": sortino_ratio(sel),
        "ci95": ci,
        "p_value": p,
        **bayes,
        **walk_forward_ok(pnls, mask, closed),
        **rolling_window_ok(pnls, mask, closed),
        **expanding_window_ok(pnls, mask, closed),
        **oos_holdout_ok(pnls, mask, closed),
        **temporal_stability(pnls, mask, closed),
        **regime_slice_ok(pnls, mask, numeric),
    }
    if out.get("pf") is None and out.get("n", 0) > 0 and float(np.sum(sel[sel > 0])) > 0:
        out["pf_inf"] = True
    out["quality_score"] = quality_score(out)
    out["edge_score"] = out["quality_score"]
    return out


def apply_fdr(candidates: list[dict[str, Any]], *, alpha: float = 0.05) -> None:
    pvals = [c.get("p_value") for c in candidates]
    qvals = benjamini_hochberg(pvals, alpha=alpha)
    for c, q in zip(candidates, qvals):
        c["q_value"] = q
        c["fdr_ok"] = bool(q is not None and float(q) <= alpha)


__all__ = [
    "apply_fdr",
    "bayesian_edge_posterior",
    "cheap_screen",
    "full_validate",
    "max_drawdown",
    "quality_score",
    "sortino_ratio",
]
