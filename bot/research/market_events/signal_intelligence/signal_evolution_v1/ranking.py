"""Automatic signal ranking / score / promotion hints."""

from __future__ import annotations

from typing import Any


def score_signal(
    *,
    rolling: dict[str, Any],
    decay: dict[str, Any],
    survival: dict[str, Any],
    drift: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    last100 = rolling.get("last_100") or rolling.get("last_50") or {}
    all_m = rolling.get("all") or {}
    ev = last100.get("ev")
    wr = last100.get("wr")
    pf = last100.get("pf")
    sharpe = last100.get("sharpe")
    surv = float(survival.get("survival_prob") or 0.0)
    drift_s = float(drift.get("drift_score") or 0.0)
    half = decay.get("half_life_days")
    slope = decay.get("slope")

    score = 50.0
    if ev is not None:
        score += max(-25.0, min(25.0, float(ev) * 8.0))
    if wr is not None:
        score += (float(wr) - 0.5) * 40.0
    if pf is not None:
        score += max(-10.0, min(15.0, (float(pf) - 1.0) * 8.0))
    if sharpe is not None:
        score += max(-10.0, min(10.0, float(sharpe) * 3.0))
    score += (surv - 0.5) * 20.0
    score -= drift_s * 20.0
    if slope is not None:
        score += max(-10.0, min(10.0, float(slope) * 50.0))
    if half is not None:
        if half < 20:
            score -= 8.0
        elif half > 90:
            score += 5.0
    if status == "DEAD":
        score -= 25.0
    elif status == "DECAY":
        score -= 10.0
    elif status == "PEAK":
        score += 8.0
    elif status == "GROWTH":
        score += 5.0

    score = round(max(0.0, min(100.0, score)), 2)
    conf = round(
        max(
            0.05,
            min(
                0.99,
                0.35 * surv
                + 0.25 * min(1.0, (all_m.get("n") or 0) / 200.0)
                + 0.20 * (1.0 - drift_s)
                + 0.20 * (float(wr or 0.5)),
            ),
        ),
        4,
    )
    return {"score": score, "confidence": conf}


def promotion_recommendation(ranked: list[dict[str, Any]]) -> dict[str, Any]:
    """Research-only recommendation. No auto promotion."""
    strong = [r for r in ranked if r.get("status") in ("PEAK", "GROWTH") and float(r.get("score") or 0) >= 65]
    weak = [r for r in ranked if r.get("status") in ("DECAY", "DEAD")]
    promote = [r for r in strong if float(r.get("confidence") or 0) >= 0.55 and float(r.get("drift") or 0) < 0.45]
    retire = [r for r in weak if r.get("status") == "DEAD" or float(r.get("score") or 0) < 35]
    if promote:
        status = "PROMOTE_WATCHLIST"
        rec = f"{len(promote)} signal(s) look strong for paper watchlist (recommendation only)."
    elif strong:
        status = "MONITOR_GROWTH"
        rec = "Some signals growing/peaking — continue shadow monitoring."
    elif retire and not strong:
        status = "RETIRE_WEAK"
        rec = "Weak/dead signals dominate — prefer retirement over promotion."
    else:
        status = "HOLD_RESEARCH"
        rec = "No clear promotion candidates yet."
    return {
        "status": status,
        "recommendation": rec,
        "auto_promotion": False,
        "promote_candidates": [r.get("signal") for r in promote[:10]],
        "retire_candidates": [r.get("signal") for r in retire[:10]],
        "n_strong": len(strong),
        "n_weak": len(weak),
    }


__all__ = ["promotion_recommendation", "score_signal"]
