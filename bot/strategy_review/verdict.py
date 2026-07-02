"""Final strategy verdict — exactly one KEEP or CHANGE decision."""

from __future__ import annotations

from typing import Any

from bot.strategy_review.constants import MIN_TRADES_SINCE_CHANGE, REVIEW_VERSION
from bot.strategy_review.safety import check_safety_gate


def _candidate_from_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    rec = entry.get("recommendation", {})
    if rec.get("action") != "CHANGE":
        return None
    cand = rec.get("candidate") or {}
    return {
        "parameter": "entry",
        "label": "CHANGE ENTRY",
        "from_value": rec.get("current"),
        "to_value": rec.get("recommended"),
        "confidence_pct": rec.get("confidence_pct", 0),
        "improvement_score": float(cand.get("profit_factor", 0)) * float(cand.get("avg_pnl", 0)),
        "reasons": rec.get("reasons", []),
        **cand,
    }


def _candidate_from_stop(stop: dict[str, Any]) -> dict[str, Any] | None:
    rec = stop.get("recommendation", {})
    if rec.get("action") != "CHANGE":
        return None
    cand = rec.get("candidate") or {}
    return {
        "parameter": "stop_loss",
        "label": "CHANGE STOP",
        "from_value": rec.get("current_stop_pct"),
        "to_value": rec.get("recommended_stop_pct"),
        "confidence_pct": rec.get("confidence_pct", 0),
        "improvement_score": float(rec.get("expected_pf_improvement_pct", 0)),
        "reasons": rec.get("reasons", []),
        "overfit_risk": rec.get("risk", cand.get("overfit_risk", "HIGH")),
        "walk_forward": cand.get("walk_forward", {}),
        "p_value": cand.get("p_value", 1.0),
        "sample_size": cand.get("trades", 0),
    }


def _candidate_from_trailing(trailing: dict[str, Any]) -> dict[str, Any] | None:
    rec = trailing.get("recommendation", {})
    if rec.get("action") != "CHANGE":
        return None
    cand = rec.get("candidate") or {}
    return {
        "parameter": "trailing",
        "label": "CHANGE TRAILING",
        "from_value": f"{rec.get('current_activation')}/{rec.get('current_distance')}",
        "to_value": f"{rec.get('recommended_activation')}/{rec.get('recommended_distance')}",
        "confidence_pct": rec.get("confidence_pct", 0),
        "improvement_score": float(cand.get("profit_factor", 0)) * float(cand.get("avg_pnl", 0)),
        "reasons": rec.get("reasons", []),
        **cand,
    }


def build_final_verdict(
    report: dict[str, Any],
    *,
    entry: dict[str, Any],
    stop: dict[str, Any],
    trailing: dict[str, Any],
) -> dict[str, Any]:
    live = report.get("live_sample", {})
    trades_since = int(live.get("current_since_change", 0))

    if trades_since < MIN_TRADES_SINCE_CHANGE:
        return {
            "decision": "KEEP CURRENT SETTINGS",
            "confidence_pct": min(95.0, 70.0 + trades_since / 20),
            "reason": (
                f"Недостаточно новых сделок после последнего изменения "
                f"({trades_since} < {MIN_TRADES_SINCE_CHANGE})."
            ),
            "next_review": f"после {MIN_TRADES_SINCE_CHANGE - trades_since} новых сделок",
            "change_parameter": None,
            "safety_blocked": True,
        }

    candidates: list[dict[str, Any]] = []
    for source, data in (("entry", entry), ("stop", stop), ("trailing", trailing)):
        if source == "entry":
            c = _candidate_from_entry(data)
        elif source == "stop":
            c = _candidate_from_stop(data)
        else:
            c = _candidate_from_trailing(data)
        if c:
            candidates.append(c)

    scored: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for cand in candidates:
        gate = check_safety_gate(report, cand)
        cand["safety_gate"] = gate
        if gate["passed"]:
            scored.append((cand, gate))

    if not scored:
        scientist = report.get("scientist", {})
        brain = report.get("trading_brain", {})
        extra_reasons = []
        if scientist.get("best_next_step", {}).get("blocked"):
            extra_reasons.append("AI Scientist: no validated experiment")
        if not brain.get("top_knowledge"):
            extra_reasons.append("Trading Brain: weak causal signal")

        return {
            "decision": "KEEP CURRENT SETTINGS",
            "confidence_pct": 85.0,
            "reason": (
                "Insufficient evidence — ни один параметр не прошёл safety gate "
                "(sample, walk-forward, overfit, confidence, p-value)."
            ),
            "detail_reasons": extra_reasons,
            "next_review": f"после {MIN_TRADES_SINCE_CHANGE} новых сделок",
            "change_parameter": None,
            "safety_blocked": True,
            "rejected_candidates": [
                {"parameter": c["parameter"], "failures": c.get("safety_gate", {}).get("failures", [])}
                for c in candidates
            ],
        }

    best, gate = max(scored, key=lambda x: (x[0]["confidence_pct"], x[0]["improvement_score"]))

    param = best["parameter"]
    if param == "entry":
        change_text = f"{best['from_value']:.2f} → {best['to_value']:.2f}"
        label = "CHANGE ENTRY"
    elif param == "stop_loss":
        change_text = f"{best['from_value']:.0f}% → {best['to_value']:.0f}%"
        label = "CHANGE STOP"
    else:
        change_text = f"{best['from_value']} → {best['to_value']}"
        label = "CHANGE TRAILING"

    return {
        "decision": label,
        "decision_full": "CHANGE SETTINGS",
        "confidence_pct": best["confidence_pct"],
        "reason": "Самое сильное статистическое улучшение. Другие параметры оставить без изменений.",
        "detail_reasons": best.get("reasons", []),
        "change_parameter": param,
        "change_text": change_text,
        "safety_gate": gate,
        "safety_blocked": False,
        "next_review": "после следующего изменения конфигурации + 300 сделок",
        "version": REVIEW_VERSION,
    }
