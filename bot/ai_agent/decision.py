"""Rule-based AI Score and decision (observe-only)."""

from __future__ import annotations

from typing import Any

ALLOW_THRESHOLD = 65.0
SKIP_THRESHOLD = 35.0


def compute_ai_score(features: dict[str, Any]) -> float:
    """Rule-based score 0–100. ML models plug in via models.py later."""
    score = 50.0
    entry = float(features.get("entry_price") or 0.4)
    spread = features.get("spread")
    btc_30 = features.get("btc_move_30s")
    side = features.get("side", "NO")
    regime = features.get("regime_label") or ""

    if entry <= 0.39:
        score += 12
    elif entry >= 0.40:
        score -= 10

    if spread is not None:
        if spread <= 0.015:
            score += 10
        elif spread > 0.03:
            score -= 15

    if btc_30 is not None:
        if side == "NO" and btc_30 < 0:
            score += 12
        elif side == "YES" and btc_30 > 0:
            score += 12
        elif side == "NO" and btc_30 > 20:
            score -= 18
        elif abs(btc_30) > 30:
            score -= 8

    if regime in ("Low Liquidity", "News Spike", "Panic"):
        score -= 12
    elif regime in ("Mean Reversion", "Range"):
        score += 5

    vol = features.get("volatility_30s")
    if vol is not None and vol > 25:
        score -= 6

    return max(0.0, min(100.0, round(score, 1)))


def compute_decision(ai_score: float) -> str:
    """
    ALLOW / SKIP / SHADOW — logged only; does not affect trading in v1.
    SHADOW = uncertain band; still observe-only until stats validate.
    """
    if ai_score >= ALLOW_THRESHOLD:
        return "ALLOW"
    if ai_score <= SKIP_THRESHOLD:
        return "SKIP"
    return "SHADOW"


def apply_decision(features: dict[str, Any]) -> dict[str, Any]:
    score = compute_ai_score(features)
    decision = compute_decision(score)
    return {**features, "ai_score": score, "decision": decision}
