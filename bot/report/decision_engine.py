"""AI Decision Engine v3 (Report §26)."""

from __future__ import annotations

from typing import Any


def build_decision_engine(report: dict[str, Any]) -> dict[str, Any]:
    live = report.get("live_sample", {})
    overfit = report.get("overfit_detector", {})
    optimizer = report.get("parameter_optimizer", {})
    regime = report.get("market_regime", {})
    walk = report.get("walk_forward", {})
    scores = report.get("final_score", {})
    positions = report.get("current_positions", {})

    overall_score = scores.get("overall", 0)
    regime_label = _dominant_regime(regime)
    reasons: list[str] = []

    if positions.get("warnings"):
        return {
            "overall_score": overall_score,
            "market_regime": regime_label,
            "recommendation": "FIX OPERATIONS",
            "confidence_pct": 95.0,
            "insufficient_live_data": False,
            "reasons": [w["message"] for w in positions["warnings"]],
            "reason": "Expired or stuck position requires recovery before strategy changes",
        }

    if not live.get("sufficient"):
        return {
            "overall_score": overall_score,
            "market_regime": regime_label,
            "recommendation": "KEEP CURRENT SETTINGS",
            "confidence_pct": 91.0,
            "insufficient_live_data": True,
            "current_sample": live.get("current_since_change", 0),
            "required_min": live.get("required_min", 300),
            "required_target": live.get("required_target", 500),
            "trades_needed": live.get("trades_needed", 0),
            "reasons": [
                f"INSUFFICIENT LIVE DATA — {live.get('current_since_change', 0)} trades since last change",
                f"Required: {live.get('required_min', 300)}–{live.get('required_target', 500)} trades",
            ],
            "reason": "KEEP CURRENT SETTINGS until sufficient live sample",
        }

    if overfit.get("level") == "HIGH":
        return {
            "overall_score": overall_score,
            "market_regime": regime_label,
            "recommendation": "KEEP CURRENT SETTINGS",
            "confidence_pct": 88.0,
            "insufficient_live_data": False,
            "overfit_level": "HIGH",
            "reasons": overfit.get("reasons", []),
            "reason": overfit.get("recommendation", "Do not change strategy"),
        }

    cur = optimizer.get("current", {})
    opt = optimizer.get("optimal", {})
    improvement = float(optimizer.get("expected_improvement_pct", 0))
    safe = report.get("safe_to_change", [])
    entry_safe = next((s for s in safe if s["parameter"] == "Entry Threshold"), None)

    walk_ok = walk.get("trend") != "degrading"
    opt_n = int(opt.get("trades", 0))
    conf = 70.0
    if walk_ok:
        conf += 10
        reasons.append("Walk Forward passed")
    if overfit.get("level") == "LOW":
        conf += 8
        reasons.append("No Overfit")
    if improvement > 5:
        reasons.append(f"PF improvement potential +{improvement:.1f}%")

    if (
        entry_safe
        and entry_safe.get("status") == "YES"
        and opt.get("entry") != cur.get("entry")
        and improvement >= 5
        and opt_n >= 30
        and walk_ok
    ):
        conf = min(95.0, conf + entry_safe.get("confidence_pct", 0) * 0.2)
        return {
            "overall_score": overall_score,
            "market_regime": regime_label,
            "recommendation": "CHANGE ENTRY",
            "change_parameter": "Entry Threshold",
            "change_from": cur.get("entry"),
            "change_to": opt.get("entry"),
            "expected_improvement_pct": round(improvement, 1),
            "confidence_pct": round(conf, 1),
            "insufficient_live_data": False,
            "reasons": reasons,
            "reason": (
                f"Entry {cur.get('entry'):.2f} → {opt.get('entry'):.2f}, "
                f"expected +{improvement:.1f}%"
            ),
        }

    return {
        "overall_score": overall_score,
        "market_regime": regime_label,
        "recommendation": "KEEP CURRENT SETTINGS",
        "confidence_pct": round(min(91.0, conf), 1),
        "insufficient_live_data": False,
        "reasons": reasons or ["No parameter passes SAFE TO CHANGE gate"],
        "reason": "KEEP CURRENT SETTINGS — no high-confidence change",
    }


def _dominant_regime(regime: dict[str, Any]) -> str:
    best = ("neutral", 0)
    for name, m in regime.items():
        if isinstance(m, dict) and m.get("trades", 0) > best[1]:
            best = (name, m["trades"])
    return best[0].replace("_", " ").title()
