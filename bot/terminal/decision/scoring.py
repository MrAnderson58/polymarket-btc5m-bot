"""Decision scoring — display confidence / risk framing from scan components."""

from __future__ import annotations

from bot.terminal.decision.models import LearningHint
from bot.terminal.scanner.models import RankComponents, ScannerResult
from bot.terminal.scanner.ranking import normalize_confidence


def display_confidence(scan: ScannerResult) -> float | None:
    """0–100 confidence for the card (from scan confidence or score)."""
    norm = normalize_confidence(scan.confidence)
    if norm is not None:
        return round(norm, 1)
    if scan.score is not None:
        return round(float(scan.score), 1)
    return None


def decision_score(scan: ScannerResult, learning: LearningHint | None = None) -> float:
    """
    Card score 0…100 — prefers scanner score; lightly nudges with learning.
    Not a new alpha model — presentation only.
    """
    base = float(scan.score or 0.0)
    if learning is None or learning.confirms is None:
        return round(max(0.0, min(100.0, base)), 1)
    nudge = 2.0 if learning.confirms else -3.0
    if learning.winrate_pct is not None:
        if learning.winrate_pct >= 55:
            nudge += 1.0
        elif learning.winrate_pct < 45:
            nudge -= 1.5
    return round(max(0.0, min(100.0, base + nudge)), 1)


def risk_budget_pct(score: float, confidence: float | None) -> float:
    """Illustrative risk % of notional for stop distance (1.0–2.5%)."""
    conf = confidence if confidence is not None else score
    # Higher conviction → slightly wider structure still capped
    raw = 2.5 - (min(100.0, max(0.0, conf)) / 100.0) * 1.2
    return round(max(1.0, min(2.5, raw)), 2)


def reward_multiples(score: float) -> tuple[float, float]:
    """TP1 / TP2 as R-multiples from score quality."""
    if score >= 90:
        return 1.5, 3.0
    if score >= 80:
        return 1.5, 2.5
    if score >= 70:
        return 1.2, 2.0
    return 1.0, 1.8


def component_strength(components: RankComponents, key: str, threshold: float = 60.0) -> bool:
    mapping = {
        "confidence": components.confidence,
        "ai": components.ai,
        "learning": components.learning,
        "trend": components.trend,
        "volume": components.volume,
        "news": components.news,
        "pattern": components.pattern,
    }
    val = mapping.get(key)
    return val is not None and float(val) >= threshold


__all__ = [
    "component_strength",
    "decision_score",
    "display_confidence",
    "reward_multiples",
    "risk_budget_pct",
]
