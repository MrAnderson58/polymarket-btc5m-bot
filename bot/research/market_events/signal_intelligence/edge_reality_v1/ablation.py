"""Incremental and leave-one-out ablation ensembles."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.edge_reality_v1.metrics import (
    delta_metrics,
    score_actions,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.signals import (
    INCREMENTAL_ORDER,
)

# Audit module → brain opinion module name(s) for "Brain without X".
_BRAIN_DROP_MAP: dict[str, frozenset[str]] = {
    "replay": frozenset({"replay"}),
    "edge": frozenset({"edge"}),
    "alpha": frozenset({"alpha"}),
    "causality": frozenset({"causality"}),
    "optimizer": frozenset({"optimizer"}),
    "validation": frozenset({"validation"}),
    "features": frozenset({"feature_store"}),
    "evolution": frozenset(),  # not a brain input; no-op drop
    "brain": frozenset(),  # cannot drop self inside brain
}


def _vote_ensemble(
    module_actions: dict[str, list[str]],
    modules: list[str],
    *,
    n: int,
) -> list[str]:
    """Majority vote among modules; BUY/SELL ties → SKIP."""
    out: list[str] = []
    for i in range(n):
        votes = {"BUY": 0, "SELL": 0, "SKIP": 0}
        for m in modules:
            a = str((module_actions.get(m) or ["SKIP"] * n)[i]).upper()
            if a not in votes:
                a = "SKIP"
            votes[a] += 1
        best = max(votes.items(), key=lambda kv: (kv[1], kv[0] != "SKIP"))
        if votes["BUY"] == votes["SELL"] and votes["BUY"] > 0:
            out.append("SKIP")
        else:
            out.append(best[0])
    return out


def incremental_value(
    module_actions: dict[str, list[str]],
    pnls: list[float],
    *,
    order: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Production → +modules one by one; measure improvement each step."""
    order = order or INCREMENTAL_ORDER
    n = len(pnls)
    prod = module_actions.get("production") or ["SKIP"] * n
    chain = ["production"]
    steps: list[dict[str, Any]] = []
    base_actions = list(prod)
    base_score = score_actions(base_actions, pnls)
    steps.append({
        "step": 0,
        "added": None,
        "modules": list(chain),
        "metrics": base_score,
        "delta_vs_prev": None,
        "delta_vs_production": None,
    })
    prev = base_score
    for i, mod in enumerate(order, start=1):
        if mod not in module_actions:
            continue
        chain = chain + [mod]
        actions = _vote_ensemble(module_actions, chain, n=n)
        metrics = score_actions(actions, pnls)
        steps.append({
            "step": i,
            "added": mod,
            "modules": list(chain),
            "metrics": metrics,
            "delta_vs_prev": delta_metrics(prev, metrics),
            "delta_vs_production": delta_metrics(base_score, metrics),
        })
        prev = metrics
    return steps


def remove_one_analysis(
    module_actions: dict[str, list[str]],
    pnls: list[float],
    *,
    full_modules: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Full ensemble vs leave-one-out; measure degradation."""
    n = len(pnls)
    mods = full_modules or [
        m for m in (
            "production", "replay", "edge", "alpha", "optimizer",
            "brain", "causality", "evolution", "features", "validation",
        )
        if m in module_actions
    ]
    full_actions = _vote_ensemble(module_actions, mods, n=n)
    full_score = score_actions(full_actions, pnls)
    rows: list[dict[str, Any]] = []
    for drop in mods:
        if drop == "production":
            continue
        kept = [m for m in mods if m != drop]
        actions = _vote_ensemble(module_actions, kept, n=n)
        metrics = score_actions(actions, pnls)
        deg = delta_metrics(full_score, metrics)  # negative delta_ev = hurt
        rows.append({
            "removed": drop,
            "metrics": metrics,
            "full_metrics": full_score,
            "degradation": deg,
            "hurt_score": -float(deg.get("delta_ev") or 0.0),
        })
    rows.sort(key=lambda r: -float(r.get("hurt_score") or 0.0))
    return rows


def brain_without_analysis(
    trades: list[dict[str, Any]],
    ctx: dict[str, Any],
    pnls: list[float],
) -> list[dict[str, Any]]:
    """Brain fused without each input module; measure degradation vs full Brain."""
    from bot.research.market_events.signal_intelligence.market_brain_v1.fusion import (
        bayesian_fusion,
        detect_conflict,
    )
    from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
        collect_opinions,
    )

    n = len(trades)
    full_actions: list[str] = []
    opinions_cache: list[list[dict[str, Any]]] = []
    for trade in trades:
        tid = int(trade.get("trade_id") or trade.get("id") or 0)
        opinions = collect_opinions(
            trade,
            replay=(ctx.get("replays") or {}).get(tid),
            edges=ctx.get("edges") or [],
            causal=(ctx.get("causality") or {}).get(tid),
            optimizer_state=ctx.get("optimizer_state") or {},
            experiments=ctx.get("experiments") or [],
        )
        opinions_cache.append(opinions)
        conflict = detect_conflict(opinions)
        fusion = bayesian_fusion(opinions, conflict=conflict)
        d = str(fusion.get("decision") or "HOLD").upper()
        full_actions.append("SKIP" if d in ("HOLD", "NO_TRADE") else d)

    full_score = score_actions(full_actions, pnls)
    rows: list[dict[str, Any]] = []
    for audit_mod, drop_set in _BRAIN_DROP_MAP.items():
        if not drop_set and audit_mod != "evolution":
            continue
        actions: list[str] = []
        for opinions in opinions_cache:
            if drop_set:
                filtered = [o for o in opinions if str(o.get("module")) not in drop_set]
            else:
                filtered = list(opinions)
            if not filtered:
                actions.append("SKIP")
                continue
            conflict = detect_conflict(filtered)
            fusion = bayesian_fusion(filtered, conflict=conflict)
            d = str(fusion.get("decision") or "HOLD").upper()
            actions.append("SKIP" if d in ("HOLD", "NO_TRADE") else d)
        metrics = score_actions(actions, pnls)
        deg = delta_metrics(full_score, metrics)
        rows.append({
            "removed": audit_mod,
            "label": f"Brain without {audit_mod.title()}",
            "metrics": metrics,
            "full_metrics": full_score,
            "degradation": deg,
            "hurt_score": -float(deg.get("delta_ev") or 0.0),
        })
    rows.sort(key=lambda r: -float(r.get("hurt_score") or 0.0))
    return rows


__all__ = [
    "brain_without_analysis",
    "incremental_value",
    "remove_one_analysis",
]