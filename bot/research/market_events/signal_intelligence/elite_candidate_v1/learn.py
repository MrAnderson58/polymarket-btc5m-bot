"""Overnight auto-learning: Score++ / Score-- from closed outcomes."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import categorize

# Research-only deltas (capped)
WIN_DELTA = 1.5
LOSS_DELTA = 2.0
BE_DELTA = 0.25


def learn_delta(*, pnl: float | None, result: str | None = None) -> float:
    """Positive = Score++, negative = Score--."""
    if result:
        r = str(result).upper()
        if r == "WIN":
            return WIN_DELTA
        if r == "LOSS":
            return -LOSS_DELTA
        if r in ("BE", "BREAKEVEN"):
            return -BE_DELTA
    if pnl is None:
        return 0.0
    try:
        p = float(pnl)
    except Exception:
        return 0.0
    if abs(p) < 1e-12:
        return -BE_DELTA
    return WIN_DELTA if p > 0 else -LOSS_DELTA


def apply_learning(
    base_score: float,
    *,
    pnl: float | None,
    result: str | None = None,
    prior_learned: float | None = None,
) -> dict[str, Any]:
    """
    Adjust score from closed trade outcome.
    Uses prior_learned if set, else base_score as starting point.
    """
    start = float(prior_learned if prior_learned is not None else base_score)
    delta = learn_delta(pnl=pnl, result=result)
    new_score = round(max(0.0, min(100.0, start + delta)), 4)
    return {
        "old_score": round(start, 4),
        "new_score": new_score,
        "delta": round(delta, 4),
        "category": categorize(new_score),
        "event": "score_up" if delta > 0 else ("score_down" if delta < 0 else "noop"),
    }


__all__ = ["WIN_DELTA", "LOSS_DELTA", "BE_DELTA", "apply_learning", "learn_delta"]
