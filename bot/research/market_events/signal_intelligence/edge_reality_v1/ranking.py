"""Complexity audit + grading + Pareto analysis."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.edge_reality_v1.signals import (
    AUDIT_MODULES,
)

# Approximate research package roots for LOC (read-only scan).
_MODULE_PATHS: dict[str, list[str]] = {
    "replay": ["bot/research/market_events/signal_intelligence/market_replay_v1"],
    "edge": [
        "bot/research/market_events/signal_intelligence/edge_discovery_v3",
        "bot/research/market_events/signal_intelligence/edge_discovery_v1",
    ],
    "alpha": [
        "bot/research/market_events/signal_intelligence/alpha_discovery_engine_v1",
        "bot/research/market_events/signal_intelligence/alpha_validation_v2",
    ],
    "optimizer": [],
    "brain": ["bot/research/market_events/signal_intelligence/market_brain_v1"],
    "causality": ["bot/research/market_events/signal_intelligence/market_causality_v1"],
    "evolution": ["bot/research/market_events/signal_intelligence/signal_evolution_v1"],
    "features": [
        "bot/research/market_events/signal_intelligence/feature_store.py",
        "bot/research/market_events/signal_intelligence/feature_recovery_v2",
    ],
    "validation": [
        "bot/research/market_events/signal_intelligence/alpha_validation_v2",
    ],
}


def _count_loc(paths: list[str]) -> int:
    total = 0
    for rel in paths:
        p = BASE_DIR / rel
        if p.is_file() and p.suffix == ".py":
            try:
                total += len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                pass
            continue
        if p.is_dir():
            for f in p.rglob("*.py"):
                if "__pycache__" in str(f):
                    continue
                try:
                    total += len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
                except Exception:
                    pass
    return total


def complexity_audit(
    attribution: list[dict[str, Any]],
    *,
    module_runtime_ms: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Runtime / LOC / edge-per-second / edge-per-1k-LOC."""
    module_runtime_ms = module_runtime_ms or {}
    by_name = {r["module"]: r for r in attribution}
    rows = []
    for m in AUDIT_MODULES:
        loc = _count_loc(_MODULE_PATHS.get(m) or [])
        rt_ms = float(module_runtime_ms.get(m) or 0.0)
        rt_s = rt_ms / 1000.0
        attr = by_name.get(m) or {}
        delta_ev = float((attr.get("vs_production") or {}).get("delta_ev") or attr.get("delta_ev") or 0.0)
        research_value = float(attr.get("research_value") or attr.get("f1") or 0.0)
        edge_value = delta_ev
        per_sec = (delta_ev / rt_s) if rt_s > 1e-9 else None
        per_1k = (delta_ev / (loc / 1000.0)) if loc > 0 else None
        rows.append({
            "module": m,
            "runtime_ms": round(rt_ms, 3),
            "memory_mb_est": round(max(1.0, loc / 500.0), 2),
            "loc": loc,
            "research_value": round(research_value, 4),
            "edge_value": round(edge_value, 6),
            "edge_per_second": round(per_sec, 4) if per_sec is not None else None,
            "edge_per_1000_loc": round(per_1k, 4) if per_1k is not None else None,
        })
    return rows


def _incremental_marginals(incremental: list[dict[str, Any]] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for step in incremental or []:
        added = step.get("added")
        if not added:
            continue
        d = (step.get("delta_vs_prev") or {}).get("delta_ev")
        out[str(added)] = float(d) if d is not None else 0.0
    return out


def _mean_redundancy(module: str, redundancy: dict[str, Any] | None) -> float:
    if not redundancy:
        return 0.0
    row = (redundancy.get("redundancy") or {}).get(module) or {}
    vals = [float(v) for k, v in row.items() if k != module]
    return float(sum(vals) / len(vals)) if vals else 0.0


def grade_modules(
    attribution: list[dict[str, Any]],
    remove_one: list[dict[str, Any]],
    complexity: list[dict[str, Any]],
    *,
    incremental: list[dict[str, Any]] | None = None,
    brain_without: list[dict[str, Any]] | None = None,
    redundancy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Assign A+..REMOVE using marginal incremental value + brain ablation + takes."""
    hurt = {r["removed"]: float(r.get("hurt_score") or 0.0) for r in remove_one}
    brain_hurt = {r["removed"]: float(r.get("hurt_score") or 0.0) for r in (brain_without or [])}
    marginal = _incremental_marginals(incremental)
    cx = {r["module"]: r for r in complexity}

    # Scale factors from corpus magnitudes
    abs_marg = [abs(v) for v in marginal.values()] or [1.0]
    scale_m = max(abs_marg) or 1.0
    abs_bh = [abs(v) for v in brain_hurt.values()] or [1.0]
    scale_b = max(abs_bh) or 1.0

    ranked = []
    for row in attribution:
        m = row["module"]
        if m == "production":
            continue
        d_ev = float((row.get("vs_production") or {}).get("delta_ev") or row.get("delta_ev") or 0.0)
        f1 = float(row.get("f1") or 0.0)
        mi = float(row.get("mutual_information") or 0.0)
        h = hurt.get(m, 0.0)
        bh = brain_hurt.get(m, 0.0)
        marg = marginal.get(m, 0.0)
        takes = row.get("precision") is not None and float(row.get("recall") or 0.0) > 0
        never_takes = row.get("precision") is None and float(row.get("recall") or 0.0) == 0.0
        red = _mean_redundancy(m, redundancy)
        loc = float((cx.get(m) or {}).get("loc") or 0.0)

        # Normalized predictive score in roughly [-100, 100]
        hurt_vals = [abs(v) for v in hurt.values()] or [1.0]
        scale_h = max(hurt_vals) or 1.0
        score = (
            40.0 * (marg / scale_m)
            + 25.0 * (bh / scale_b)
            + 15.0 * (h / scale_h)
            + 10.0 * f1
            + 5.0 * mi
            - 15.0 * max(0.0, red - 0.85)
            - (10.0 if never_takes else 0.0)
            - (5.0 if loc > 2000 and marg <= 0 else 0.0)
        )

        # Hard rules from mathematics of this run
        if never_takes and abs(marg) < 1e-9 and bh <= 0:
            grade = "REMOVE"
        elif marg < -0.05 * scale_m and bh <= 0:
            grade = "REMOVE"
        elif marg > 0.25 * scale_m or bh > 0.5 * scale_b:
            grade = "A+"
        elif marg > 0.05 * scale_m or bh > 0.15 * scale_b or (takes and f1 >= 0.5):
            grade = "A"
        elif marg > 0 or bh > 0 or (takes and d_ev > 0):
            grade = "B"
        elif never_takes or red >= 0.95:
            grade = "D"
        elif score >= 0:
            grade = "C"
        else:
            grade = "REMOVE"

        ranked.append({
            "module": m,
            "grade": grade,
            "score": round(score, 4),
            "delta_ev": d_ev,
            "marginal_ev": round(marg, 6),
            "brain_hurt": round(bh, 6),
            "f1": row.get("f1"),
            "mutual_information": row.get("mutual_information"),
            "hurt_if_removed": h,
            "mean_redundancy": round(red, 4),
            "never_takes": never_takes,
            "keep": grade not in ("D", "REMOVE"),
            "remove": grade in ("REMOVE", "D"),
        })
    ranked.sort(key=lambda r: -float(r["score"]))
    return ranked


def pareto_analysis(ranked: list[dict[str, Any]]) -> dict[str, Any]:
    """Smallest prefix of modules capturing ~80% of positive marginal + brain power."""
    # Prefer marginal_ev + brain_hurt as true predictive power (not solo SKIP artifact)
    def power(r: dict[str, Any]) -> float:
        return max(0.0, float(r.get("marginal_ev") or 0.0)) + max(0.0, float(r.get("brain_hurt") or 0.0))

    ordered = sorted(ranked, key=lambda r: -power(r))
    positive = [r for r in ordered if power(r) > 0]
    if not positive:
        positive = [r for r in ordered if float(r.get("delta_ev") or 0) > 0]
        def power(r: dict[str, Any]) -> float:  # noqa: F811
            return max(0.0, float(r.get("delta_ev") or 0.0))

    total = sum(power(r) for r in positive) or 1.0
    acc = 0.0
    selected: list[str] = []
    shares: list[dict[str, Any]] = []
    for r in positive:
        contrib = power(r)
        acc += contrib
        selected.append(r["module"])
        shares.append({
            "module": r["module"],
            "contrib": round(contrib, 6),
            "cum_share": round(acc / total, 4),
        })
        if acc / total >= 0.80:
            break
    n_all = max(1, len(ranked))
    return {
        "pareto_modules": selected,
        "n_pareto": len(selected),
        "n_all": n_all,
        "pareto_fraction": round(len(selected) / n_all, 4),
        "power_captured": round(min(1.0, acc / total), 4),
        "shares": shares,
        "rule": "20% modules → 80% power (cumulative max(marginal_ev,0)+max(brain_hurt,0))",
    }


def simplification_gain(ranked: list[dict[str, Any]], remove_one: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate performance if REMOVE/D modules dropped from research stack."""
    to_remove = [r["module"] for r in ranked if r.get("remove")]
    to_keep = [r["module"] for r in ranked if r.get("keep")]
    # Dropping modules with negative marginal recovers that ensemble damage;
    # never-take SKIP clones free complexity with ~0 predictive loss.
    freed = 0.0
    for r in ranked:
        if not r.get("remove"):
            continue
        marg = float(r.get("marginal_ev") or 0.0)
        if marg < 0:
            freed += -marg
        # solo SKIP artifact is not "gain" — complexity only
    return {
        "modules_to_remove": to_remove,
        "modules_to_keep": to_keep,
        "estimated_ev_gain_after_simplification": round(freed, 6),
        "n_remove": len(to_remove),
        "n_keep": len(to_keep),
    }


__all__ = [
    "complexity_audit",
    "grade_modules",
    "pareto_analysis",
    "simplification_gain",
]
