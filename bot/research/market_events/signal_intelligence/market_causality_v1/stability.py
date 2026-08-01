"""Causal stability: walk-forward, bootstrap, permutation, regimes."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_causality_v1.features import (
    CAUSE_FEATURES,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.graph import (
    build_causal_graph,
)


def _top_edge_set(graph: dict[str, Any], n: int = 10) -> set[tuple[str, str]]:
    return {
        (str(e["source"]), str(e["target"]))
        for e in (graph.get("edges") or [])[:n]
    }


def walk_forward_stability(
    rows: list[dict[str, Any]],
    *,
    n_folds: int = 4,
) -> dict[str, Any]:
    if len(rows) < n_folds * 5:
        return {"ok": False, "stability": 0.0, "folds": []}
    ordered = sorted(rows, key=lambda r: int(r.get("entry_ts") or r.get("closed_at") or 0))
    chunk = max(1, len(ordered) // n_folds)
    base = _top_edge_set(build_causal_graph(ordered))
    folds = []
    overlaps = []
    for f in range(n_folds):
        lo = f * chunk
        hi = len(ordered) if f == n_folds - 1 else (f + 1) * chunk
        part = ordered[lo:hi]
        if len(part) < 5:
            continue
        g = build_causal_graph(part)
        s = _top_edge_set(g)
        ov = len(base & s) / max(1, len(base))
        overlaps.append(ov)
        folds.append({"fold": f, "n": len(part), "overlap": round(ov, 4), "n_edges": g.get("n_edges")})
    stab = float(np.mean(overlaps)) if overlaps else 0.0
    return {
        "ok": bool(stab >= 0.3),
        "stability": round(stab, 4),
        "folds": folds,
    }


def bootstrap_stability(
    rows: list[dict[str, Any]],
    *,
    n_boot: int = 12,
    seed: int = 0,
) -> dict[str, Any]:
    if len(rows) < 10:
        return {"ok": False, "stability": 0.0}
    rng = np.random.default_rng(seed)
    base = _top_edge_set(build_causal_graph(rows))
    overlaps = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(rows), size=len(rows))
        sample = [rows[int(i)] for i in idx]
        s = _top_edge_set(build_causal_graph(sample))
        overlaps.append(len(base & s) / max(1, len(base)))
    stab = float(np.mean(overlaps))
    return {
        "ok": bool(stab >= 0.35),
        "stability": round(stab, 4),
        "n_boot": n_boot,
        "ci95": (
            round(float(np.quantile(overlaps, 0.025)), 4),
            round(float(np.quantile(overlaps, 0.975)), 4),
        ),
    }


def permutation_stability(
    rows: list[dict[str, Any]],
    *,
    n_perm: int = 20,
    seed: int = 1,
) -> dict[str, Any]:
    """Permute outcomes; true graph edge weight to outcome should exceed null."""
    if len(rows) < 10:
        return {"ok": False, "p_value": None}
    rng = np.random.default_rng(seed)
    pnls = np.asarray([float(r.get("pnl") or 0.0) for r in rows], dtype=float)
    true_g = build_causal_graph(rows, outcomes=pnls)
    # Sum of weights into outcome
    true_w = sum(float(e["weight"]) for e in true_g["edges"] if e["target"] == "outcome")
    extreme = 0
    for _ in range(n_perm):
        shuffled = pnls.copy()
        rng.shuffle(shuffled)
        g = build_causal_graph(rows, outcomes=shuffled)
        w = sum(float(e["weight"]) for e in g["edges"] if e["target"] == "outcome")
        if w >= true_w - 1e-12:
            extreme += 1
    p = (extreme + 1) / (n_perm + 1)
    return {
        "ok": bool(p <= 0.15),
        "p_value": round(p, 6),
        "true_outcome_weight": round(true_w, 6),
        "n_perm": n_perm,
    }


def regime_stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_reg: dict[str, list] = {}
    for r in rows:
        reg = str(r.get("regime") or "UNKNOWN") or "UNKNOWN"
        by_reg.setdefault(reg, []).append(r)
    base = _top_edge_set(build_causal_graph(rows))
    per = {}
    overlaps = []
    for reg, part in by_reg.items():
        if len(part) < 8:
            continue
        s = _top_edge_set(build_causal_graph(part))
        ov = len(base & s) / max(1, len(base))
        overlaps.append(ov)
        per[reg] = {"n": len(part), "overlap": round(ov, 4)}
    stab = float(np.mean(overlaps)) if overlaps else 0.0
    return {
        "ok": bool(stab >= 0.25 or not overlaps),
        "stability": round(stab, 4),
        "regimes": per,
    }


def evaluate_stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wf = walk_forward_stability(rows)
    boot = bootstrap_stability(rows, n_boot=12 if len(rows) < 500 else 20)
    perm = permutation_stability(rows, n_perm=15 if len(rows) < 500 else 25)
    reg = regime_stability(rows)
    checks = [wf.get("ok"), boot.get("ok"), perm.get("ok"), reg.get("ok")]
    # Reject unstable relations if majority fail
    stable = sum(1 for c in checks if c) >= 2
    return {
        "stable": stable,
        "walk_forward": wf,
        "bootstrap": boot,
        "permutation": perm,
        "regimes": reg,
        "n_checks_passed": sum(1 for c in checks if c),
    }


def filter_stable_edges(
    graph: dict[str, Any],
    stability: dict[str, Any],
) -> list[dict[str, Any]]:
    """If overall unstable, keep only high-weight edges; else keep all top edges."""
    edges = list(graph.get("edges") or [])
    if stability.get("stable"):
        return edges
    # Reject weak edges under instability
    return [e for e in edges if float(e.get("weight") or 0) >= 0.15]


__all__ = [
    "bootstrap_stability",
    "evaluate_stability",
    "filter_stable_edges",
    "permutation_stability",
    "regime_stability",
    "walk_forward_stability",
]
