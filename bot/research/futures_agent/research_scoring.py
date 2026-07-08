"""Source scoring utilities for Stage 3 (schema populated in Phase C)."""

from __future__ import annotations

import math
from dataclasses import dataclass


MIN_SAMPLE_SIZE_DEFAULT = 30


@dataclass
class SourceScoreInputs:
    wins: int
    total: int
    avg_mfe: float | None = None
    avg_mae: float | None = None
    prior_rate: float = 0.5
    prior_weight: float = 5.0


def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float | None:
    """Wilson score interval lower bound for binomial proportion."""
    if total <= 0:
        return None
    p = wins / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denom)


def bayesian_shrinkage_rate(
    wins: int,
    total: int,
    *,
    prior_rate: float = 0.5,
    prior_weight: float = 5.0,
) -> float | None:
    if total < 0:
        return None
    return (wins + prior_rate * prior_weight) / (total + prior_weight)


def expectancy_proxy(avg_mfe: float | None, avg_mae: float | None) -> float | None:
    if avg_mfe is None or avg_mae is None:
        return None
    return avg_mfe - avg_mae


def recency_weighted_score(
    scores: list[tuple[float, float]],
    *,
    half_life_days: float = 90.0,
) -> float | None:
    """Weighted average where each (score, age_days) decays by half-life."""
    if not scores or half_life_days <= 0:
        return None
    lam = math.log(2) / half_life_days
    num = 0.0
    den = 0.0
    for score, age_days in scores:
        w = math.exp(-lam * max(0.0, age_days))
        num += score * w
        den += w
    return num / den if den > 0 else None


def meets_minimum_sample(total: int, minimum: int = MIN_SAMPLE_SIZE_DEFAULT) -> bool:
    return total >= minimum


def compose_source_score(inputs: SourceScoreInputs) -> dict[str, float | None]:
    """Derive score fields for futures_agent_source_scores (not persisted in Phase B)."""
    directional_accuracy = (
        inputs.wins / inputs.total if inputs.total > 0 else None
    )
    return {
        "directional_accuracy": directional_accuracy,
        "wilson_lower_bound": wilson_lower_bound(inputs.wins, inputs.total),
        "expectancy_proxy": expectancy_proxy(inputs.avg_mfe, inputs.avg_mae),
        "bayesian_rate": bayesian_shrinkage_rate(
            inputs.wins,
            inputs.total,
            prior_rate=inputs.prior_rate,
            prior_weight=inputs.prior_weight,
        ),
    }
