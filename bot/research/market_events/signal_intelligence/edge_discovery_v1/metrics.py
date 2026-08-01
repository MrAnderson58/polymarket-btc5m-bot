"""Edge metrics: PF/EV/WR/Sharpe/MaxDD, bootstrap, permutation, WF, OOS, FDR, score."""

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
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)


def pnl_of(rows: Sequence[dict[str, Any]]) -> list[float]:
    return [float(_safe_float(r.get("pnl")) or 0.0) for r in rows]


def max_drawdown(pnls: Sequence[float]) -> float | None:
    if not pnls:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += float(p)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(float(max_dd), 4)


def walk_forward_stability(
    rows: Sequence[dict[str, Any]],
    mask: Sequence[bool],
    *,
    n_folds: int = 4,
) -> dict[str, Any]:
    """Time-ordered folds: fraction with EV > 0 and mean fold EV."""
    indexed = [(i, r) for i, r in enumerate(rows) if mask[i]]
    if len(indexed) < max(8, n_folds * 2):
        return {"wf_ok": False, "wf_pos_frac": None, "wf_mean_ev": None, "folds": []}
    indexed.sort(key=lambda t: int(t[1].get("closed_at") or t[0]))
    folds: list[dict[str, Any]] = []
    chunk = max(1, len(indexed) // n_folds)
    for f in range(n_folds):
        lo = f * chunk
        hi = len(indexed) if f == n_folds - 1 else (f + 1) * chunk
        part = indexed[lo:hi]
        if len(part) < 3:
            continue
        ev = float(np.mean([float(_safe_float(r.get("pnl")) or 0.0) for _, r in part]))
        folds.append({"fold": f, "n": len(part), "ev": round(ev, 4)})
    if not folds:
        return {"wf_ok": False, "wf_pos_frac": None, "wf_mean_ev": None, "folds": []}
    pos = sum(1 for x in folds if (x.get("ev") or 0) > 0)
    frac = pos / len(folds)
    mean_ev = float(np.mean([x["ev"] for x in folds]))
    return {
        "wf_ok": frac >= 0.5 and mean_ev > 0,
        "wf_pos_frac": round(frac, 4),
        "wf_mean_ev": round(mean_ev, 4),
        "folds": folds,
    }


def oos_holdout(
    rows: Sequence[dict[str, Any]],
    mask: Sequence[bool],
    baseline_pnls: Sequence[float],
    *,
    holdout_frac: float = 0.30,
) -> dict[str, Any]:
    """Last holdout_frac by time = OOS; compare EV to baseline OOS mean."""
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda t: int(t[1].get("closed_at") or t[0]))
    n = len(indexed)
    if n < 10:
        return {"oos_ok": False, "oos_n": 0, "oos_ev": None, "oos_pf": None}
    cut = int(n * (1.0 - holdout_frac))
    oos_idx = {i for i, _ in indexed[cut:]}
    sel = [rows[i] for i in oos_idx if mask[i]]
    base_oos = [float(_safe_float(rows[i].get("pnl")) or 0.0) for i in oos_idx]
    m = trade_metrics(pnl_of(sel))
    base_ev = float(np.mean(base_oos)) if base_oos else 0.0
    ev = m.get("expectancy")
    oos_ok = bool(ev is not None and float(ev) > base_ev and float(ev) > 0 and int(m["n"]) >= 3)
    return {
        "oos_ok": oos_ok,
        "oos_n": m["n"],
        "oos_ev": m.get("expectancy"),
        "oos_pf": m.get("pf"),
        "oos_base_ev": round(base_ev, 4),
    }


def temporal_stability(
    rows: Sequence[dict[str, Any]],
    mask: Sequence[bool],
    *,
    n_buckets: int = 5,
) -> dict[str, Any]:
    indexed = [(i, r) for i, r in enumerate(rows) if mask[i]]
    if len(indexed) < n_buckets * 2:
        return {"stable": False, "stability": 0.0, "cv": None}
    indexed.sort(key=lambda t: int(t[1].get("closed_at") or t[0]))
    chunk = max(1, len(indexed) // n_buckets)
    evs: list[float] = []
    for b in range(n_buckets):
        lo = b * chunk
        hi = len(indexed) if b == n_buckets - 1 else (b + 1) * chunk
        part = indexed[lo:hi]
        if len(part) < 2:
            continue
        evs.append(float(np.mean([float(_safe_float(r.get("pnl")) or 0.0) for _, r in part])))
    if len(evs) < 2:
        return {"stable": False, "stability": 0.0, "cv": None}
    mean = float(np.mean(evs))
    std = float(np.std(evs))
    cv = abs(std / mean) if abs(mean) > 1e-9 else 99.0
    pos_frac = sum(1 for e in evs if e > 0) / len(evs)
    # Lower CV + positive buckets => higher stability in [0,1]
    stability = max(0.0, min(1.0, pos_frac * (1.0 / (1.0 + cv))))
    return {
        "stable": bool(pos_frac >= 0.6 and cv < 2.5),
        "stability": round(stability, 4),
        "cv": round(cv, 4),
        "pos_frac": round(pos_frac, 4),
    }


def edge_score(candidate: dict[str, Any]) -> float:
    """0..100 composite from PF, EV, WR, stability, sample, robustness, OOS, WF."""
    pf = effective_pf(candidate) if candidate.get("pf") is not None or candidate.get("pf_inf") else 0.0
    if candidate.get("pf_inf"):
        pf = 10.0
    ev = float(candidate.get("expectancy") or 0.0)
    wr = float(candidate.get("winrate") or 0.0) / 100.0
    n = int(candidate.get("n") or 0)
    stab = float(candidate.get("stability") or 0.0)
    p = candidate.get("p_value")
    robust = 1.0 - float(p) if p is not None else (0.5 if (candidate.get("ci95") or (None, None))[0] else 0.0)
    oos = 1.0 if candidate.get("oos_ok") else 0.0
    wf = 1.0 if candidate.get("wf_ok") else 0.0

    def clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    score = 100.0 * (
        0.18 * clamp01(pf / 2.5)
        + 0.18 * clamp01((ev + 1.0) / 3.0)
        + 0.10 * clamp01(wr)
        + 0.14 * clamp01(stab)
        + 0.12 * clamp01(n / 400.0)
        + 0.12 * clamp01(robust)
        + 0.08 * oos
        + 0.08 * wf
    )
    return round(float(score), 2)


def evaluate_mask(
    rows: Sequence[dict[str, Any]],
    mask: Sequence[bool],
    baseline_pnls: Sequence[float],
    *,
    seed: int = 0,
    n_boot: int = 120,
    n_perm: int = 80,
) -> dict[str, Any]:
    selected = [rows[i] for i, m in enumerate(mask) if m]
    pnls = pnl_of(selected)
    m = trade_metrics(pnls)
    ci = bootstrap_expectancy_ci(pnls, n_boot=n_boot, seed=seed)
    p = permutation_pvalue(pnls, baseline_pnls, n_perm=n_perm, seed=seed + 7)
    wf = walk_forward_stability(rows, mask)
    oos = oos_holdout(rows, mask, baseline_pnls)
    stab = temporal_stability(rows, mask)
    out = {
        **m,
        "max_dd": max_drawdown(pnls),
        "ci95": ci,
        "p_value": p,
        **wf,
        **oos,
        **stab,
    }
    out["edge_score"] = edge_score(out)
    return out


def apply_fdr(candidates: list[dict[str, Any]], *, alpha: float = 0.05) -> None:
    pvals = [c.get("p_value") for c in candidates]
    qvals = benjamini_hochberg(pvals, alpha=alpha)
    for c, q in zip(candidates, qvals):
        c["q_value"] = q
        c["fdr_ok"] = bool(q is not None and float(q) <= alpha)


__all__ = [
    "apply_fdr",
    "edge_score",
    "evaluate_mask",
    "max_drawdown",
    "pnl_of",
]
