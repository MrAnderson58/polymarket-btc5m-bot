"""Ranking Engine — score hypotheses after validation."""

from __future__ import annotations

from typing import Any

from bot.scientist.constants import MIN_SAMPLE_TRADES


def rank_hypothesis(hypothesis: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    checks = validation.get("checks", {})
    confidence = float(hypothesis.get("confidence", 0))
    expected_pf = float(hypothesis.get("expected_pf", 1.0))
    improvement = float(hypothesis.get("expected_improvement", 0))
    sample_n = int(checks.get("sample_size", {}).get("n", hypothesis.get("sample_n", 0)))

    score = confidence * 0.35
    score += min(30.0, max(0.0, improvement * 2))
    score += min(20.0, (expected_pf - 1.0) * 15)
    if validation.get("eligible_for_recommendation"):
        score += 15
    if checks.get("walk_forward", {}).get("passed"):
        score += 8
    if checks.get("stability", {}).get("passed"):
        score += 5
    if sample_n >= MIN_SAMPLE_TRADES:
        score += 10
    elif sample_n < 30:
        score -= 15

    overfit_risk = checks.get("overfit", {}).get("risk", "HIGH")
    if overfit_risk == "HIGH":
        score -= 25
    elif overfit_risk == "MEDIUM":
        score -= 8

    score = max(0.0, min(100.0, score))

    if score >= 75 and validation.get("eligible_for_recommendation"):
        priority = "HIGH"
    elif score >= 50:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    risk = overfit_risk
    if sample_n < MIN_SAMPLE_TRADES:
        risk = "HIGH" if sample_n < 50 else "MEDIUM"

    return {
        "score": round(score, 1),
        "priority": priority,
        "risk": risk,
        "expected_pf_pct": round((expected_pf - 1.0) * 100, 1),
        "confidence_pct": round(confidence, 1),
    }


def rank_experiments(experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(experiments, key=lambda e: float(e.get("ranking_score", 0)), reverse=True)
