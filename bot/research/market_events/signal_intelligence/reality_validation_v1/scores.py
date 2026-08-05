"""PART 8–10 — Overfitting / Reality scores + fail reasons (research-only)."""

from __future__ import annotations

from typing import Any


def compute_overfitting_score(parts: dict[str, Any]) -> dict[str, Any]:
    """
    Overfitting score 0–100 (higher = more overfit / less generalizable).
    """
    score = 0.0
    notes: list[str] = []

    wf = parts.get("walk_forward") or {}
    oos = parts.get("oos") or {}
    mc = parts.get("monte_carlo") or {}
    stress = parts.get("stress") or {}
    regimes = parts.get("regimes") or {}
    loco = parts.get("leave_one_coin") or {}
    lomo = parts.get("leave_one_month") or {}

    # Walk-forward gap
    gap = wf.get("gap")
    if gap is not None and gap > 0:
        bump = min(25.0, float(gap) * 50.0)
        score += bump
        notes.append(f"wf_gap={gap}")
    if wf.get("val_positive_rate") is not None and float(wf["val_positive_rate"]) < 0.5:
        score += 15.0
        notes.append("wf_val_weak")

    # OOS degradation
    if oos.get("degradation"):
        score += 20.0
        notes.append("oos_degradation")
    gs = oos.get("gap_sharpe")
    if gs is not None and gs > 1.0:
        score += min(15.0, float(gs) * 3.0)
        notes.append(f"oos_sharpe_gap={gs}")

    # MC fragility
    if mc.get("fragile"):
        score += 15.0
        notes.append("mc_fragile")
    mix = (mc.get("mixed") or {}).get("p_profit")
    if mix is not None and float(mix) < 0.6:
        score += 10.0
        notes.append(f"mc_p_profit={mix}")

    # Stress survive
    sr = stress.get("survive_rate")
    if sr is not None and float(sr) < 0.5:
        score += 15.0
        notes.append(f"stress_survive={sr}")

    # Regime concentration
    if regimes.get("fragile"):
        score += 10.0
        notes.append("regime_fragile")

    # LOCO / LOMO
    if loco.get("fragile"):
        score += 10.0
        notes.append("loco_fragile")
    if lomo.get("fragile"):
        score += 10.0
        notes.append("lomo_fragile")

    score = max(0.0, min(100.0, round(score, 2)))
    # generalization gap proxy
    gen_gap = 0.0
    if gap is not None:
        gen_gap += abs(float(gap))
    if oos.get("gap_expectancy") is not None:
        gen_gap += abs(float(oos["gap_expectancy"]))
    stability = 100.0 - score
    confidence = max(0.0, min(100.0, stability * (1.0 if not oos.get("degradation") else 0.7)))
    return {
        "overfitting_score": score,
        "generalization_gap": round(gen_gap, 6),
        "stability": round(stability, 2),
        "confidence": round(confidence, 2),
        "notes": notes,
    }


def compute_reality_score(parts: dict[str, Any], overfit: dict[str, Any]) -> dict[str, Any]:
    """
    Reality / research confidence 0–100 (higher = more trustworthy).
    Starts at 100; subtracts for each failed stress / instability.
    """
    score = 100.0
    deductions: list[dict[str, Any]] = []

    def deduct(pts: float, reason: str) -> None:
        nonlocal score
        pts = float(pts)
        if pts <= 0:
            return
        score -= pts
        deductions.append({"points": pts, "reason": reason})

    wf = parts.get("walk_forward") or {}
    oos = parts.get("oos") or {}
    mc = parts.get("monte_carlo") or {}
    stress = parts.get("stress") or {}
    regimes = parts.get("regimes") or {}
    loco = parts.get("leave_one_coin") or {}
    lomo = parts.get("leave_one_month") or {}

    if not wf.get("ok"):
        deduct(5, "walk_forward_unavailable")
    else:
        if (wf.get("val_positive_rate") or 1) < 0.45:
            deduct(12, "walk_forward_validation_weak")
        if (wf.get("gap") or 0) > 0.5:
            deduct(8, "walk_forward_large_gap")

    if not oos.get("ok"):
        deduct(5, "oos_unavailable")
    else:
        if oos.get("degradation"):
            deduct(18, "oos_performance_collapse")
        new = oos.get("new") or {}
        if (new.get("pnl") or 0) < 0:
            deduct(10, "oos_new_period_negative")
        ge = oos.get("gap_expectancy")
        if ge is not None and float(ge) > 0.15:
            deduct(min(10.0, float(ge) * 20.0), "oos_expectancy_gap")

    if mc.get("fragile"):
        deduct(12, "monte_carlo_edge_fragile")
    mix = (mc.get("mixed") or {}).get("p_profit")
    if mix is not None and float(mix) < 0.5:
        deduct(10, "monte_carlo_coin_flip")

    sr = stress.get("survive_rate")
    if sr is not None:
        if float(sr) < 0.3:
            deduct(15, "stress_mostly_fails")
        elif float(sr) < 0.6:
            deduct(8, "stress_partial_fail")
    # Relative damage even if still profitable
    weakest = stress.get("weakest") or {}
    scenarios = stress.get("scenarios") or []
    baseline = next((s for s in scenarios if s.get("stress") == "baseline"), None)
    if baseline and weakest:
        bp = float(baseline.get("pnl") or 0)
        wp = float(weakest.get("pnl") or 0)
        if abs(bp) > 1e-12:
            drop = (bp - wp) / abs(bp)
            if drop >= 0.5:
                deduct(10, f"stress_halves_edge:{weakest.get('stress')}")
            elif drop >= 0.35:
                deduct(6, f"stress_cuts_edge:{weakest.get('stress')}")

    if regimes.get("fragile"):
        deduct(8, "regime_concentration")

    if loco.get("fragile"):
        deduct(8, "coin_concentration")
    if lomo.get("fragile"):
        deduct(8, "month_concentration")

    of = float(overfit.get("overfitting_score") or 0)
    if of >= 70:
        deduct(12, "high_overfitting")
    elif of >= 40:
        deduct(6, "moderate_overfitting")

    score = max(0.0, min(100.0, round(score, 2)))
    return {
        "reality_score": score,
        "deductions": deductions,
        "research_confidence": score,
    }


def fail_reasons(parts: dict[str, Any], overfit: dict[str, Any], reality: dict[str, Any]) -> list[dict[str, Any]]:
    """Top reasons the system may fail in reality."""
    reasons: list[dict[str, Any]] = []

    for d in reality.get("deductions") or []:
        reasons.append({
            "severity": float(d.get("points") or 0),
            "reason": str(d.get("reason")),
            "source": "reality_score",
        })

    stress = parts.get("stress") or {}
    scenarios = stress.get("scenarios") or []
    baseline = next((s for s in scenarios if s.get("stress") == "baseline"), None)
    base_pnl = float((baseline or {}).get("pnl") or 0.0)
    weakest = stress.get("weakest") or {}
    if weakest:
        weak_pnl = float(weakest.get("pnl") or 0.0)
        if abs(base_pnl) > 1e-12:
            drop_frac = max(0.0, (base_pnl - weak_pnl) / abs(base_pnl))
        else:
            drop_frac = 0.0
        reasons.append({
            "severity": round(8.0 + min(25.0, drop_frac * 40.0), 4),
            "reason": f"stress_weakest:{weakest.get('stress')}",
            "source": "stress",
            "detail": {"drop_frac": round(drop_frac, 4), "weak_pnl": weak_pnl, "base_pnl": base_pnl},
        })

    oos = parts.get("oos") or {}
    if oos.get("degradation"):
        reasons.append({
            "severity": 20.0,
            "reason": "out_of_sample_edge_dies",
            "source": "oos",
        })
    elif oos.get("gap_expectancy") is not None and float(oos["gap_expectancy"]) > 0.1:
        reasons.append({
            "severity": round(min(18.0, float(oos["gap_expectancy"]) * 40.0), 4),
            "reason": "oos_expectancy_gap",
            "source": "oos",
        })

    mc = parts.get("monte_carlo") or {}
    if mc.get("fragile"):
        reasons.append({
            "severity": 15.0,
            "reason": "path_dependent_or_order_luck",
            "source": "monte_carlo",
        })

    if (parts.get("regimes") or {}).get("fragile"):
        reasons.append({
            "severity": 12.0,
            "reason": "edge_lives_in_one_regime_only",
            "source": "regimes",
        })

    if (parts.get("leave_one_coin") or {}).get("fragile"):
        reasons.append({
            "severity": 12.0,
            "reason": "results_hinge_on_single_coin",
            "source": "loco",
        })

    if (parts.get("leave_one_month") or {}).get("fragile"):
        reasons.append({
            "severity": 12.0,
            "reason": "results_hinge_on_single_month",
            "source": "lomo",
        })

    wf = parts.get("walk_forward") or {}
    if wf.get("gap") is not None and float(wf["gap"]) > 0.05:
        reasons.append({
            "severity": round(min(15.0, float(wf["gap"]) * 80.0), 4),
            "reason": "walk_forward_train_val_gap",
            "source": "walk_forward",
        })

    if float(overfit.get("overfitting_score") or 0) >= 50:
        reasons.append({
            "severity": float(overfit["overfitting_score"]) * 0.2,
            "reason": "overfitting_risk",
            "source": "overfit",
        })

    reasons.sort(key=lambda r: float(r.get("severity") or 0), reverse=True)
    # dedupe by reason
    seen = set()
    uniq: list[dict[str, Any]] = []
    for r in reasons:
        k = r["reason"]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq[:12]


__all__ = ["compute_overfitting_score", "compute_reality_score", "fail_reasons"]
