"""Unified ranking — feature mix → score 0…100 (no trading logic)."""

from __future__ import annotations

from bot.terminal.scanner.models import RankComponents

# Weights sum to 1.0
_WEIGHTS: dict[str, float] = {
    "confidence": 0.25,
    "ai": 0.20,
    "learning": 0.10,
    "trend": 0.15,
    "volume": 0.10,
    "news": 0.10,
    "pattern": 0.10,
}


def _clamp(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))


def normalize_confidence(raw: float | None) -> float | None:
    """Map G3.1-style confidence (often 0–10) into 0–100."""
    if raw is None:
        return None
    v = float(raw)
    if v <= 10.0:
        return _clamp(v * 10.0)
    return _clamp(v)


def compute_score(components: RankComponents) -> float:
    """
    Weighted score from available components.

    Missing features redistribute weight across present ones.
    Returns 0…100.
    """
    values: dict[str, float] = {}
    mapping = {
        "confidence": components.confidence,
        "ai": components.ai,
        "learning": components.learning,
        "trend": components.trend,
        "volume": components.volume,
        "news": components.news,
        "pattern": components.pattern,
    }
    for key, raw in mapping.items():
        clamped = _clamp(raw)
        if clamped is not None:
            values[key] = clamped
    if not values:
        return 0.0
    weight_sum = sum(_WEIGHTS[k] for k in values)
    if weight_sum <= 0:
        return 0.0
    score = sum(values[k] * (_WEIGHTS[k] / weight_sum) for k in values)
    return round(score, 1)


__all__ = ["compute_score", "normalize_confidence", "WEIGHTS"]

WEIGHTS = _WEIGHTS
