"""Current-state prediction + adaptive research recommendation."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.market_regime_transition_v1.states import (
    canon_state,
)


def predict_next(markov: dict[str, Any], *, current: str | None = None) -> dict[str, Any]:
    cur = current or markov.get("current_state") or "UNKNOWN"
    probs = (markov.get("matrix") or {}).get(cur) or {}
    ranked = sorted(
        ((s, float(p)) for s, p in probs.items() if float(p) > 0),
        key=lambda x: x[1],
        reverse=True,
    )
    return {
        "current": cur,
        "predictions": [{"state": s, "prob_pct": round(p * 100.0, 1)} for s, p in ranked],
        "top": ranked[0][0] if ranked else "UNKNOWN",
        "top_prob_pct": round(ranked[0][1] * 100.0, 1) if ranked else 0.0,
    }


def adaptive_recommendation(
    *,
    markov: dict[str, Any],
    prediction: dict[str, Any],
    top_transitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Research-only bias from Markov + READY transitions."""
    cur = prediction.get("current") or markov.get("current_state") or "UNKNOWN"
    top = prediction.get("top") or "UNKNOWN"
    top_p = float(prediction.get("top_prob_pct") or 0) / 100.0

    ready = [t for t in (top_transitions or []) if t.get("ready") and t.get("from_state") == cur]
    hist_wr = hist_pf = hist_ev = None
    if ready:
        best = max(ready, key=lambda r: float(r.get("score") or 0))
        hist_wr, hist_pf, hist_ev = best.get("wr"), best.get("pf"), best.get("ev")

    bias = "NO TRADE"
    if top in ("STRONG_BULL", "WEAK_BULL") and top_p >= 0.35:
        bias = "LONG"
    elif top in ("STRONG_BEAR", "WEAK_BEAR") and top_p >= 0.35:
        bias = "SHORT"
    elif top == "RANGE" or top_p < 0.30:
        bias = "NO TRADE"

    # If READY transitions strongly disagree, stay research-only
    if ready and hist_ev is not None and float(hist_ev) < 0:
        bias = "NO TRADE"

    conf = round(min(0.99, max(0.05, top_p)), 4)
    return {
        "current_regime": cur,
        "transition_probability": round(top_p, 4),
        "next_state": top,
        "confidence": conf,
        "historical_wr": hist_wr,
        "historical_pf": hist_pf,
        "historical_ev": hist_ev,
        "recommended_bias": bias,
        "recommendation": "RESEARCH ONLY",
        "research_only": True,
    }


def current_state_from_rows(ordered: list[dict[str, Any]]) -> str:
    if not ordered:
        return "UNKNOWN"
    return canon_state(ordered[-1])


__all__ = ["adaptive_recommendation", "current_state_from_rows", "predict_next"]
