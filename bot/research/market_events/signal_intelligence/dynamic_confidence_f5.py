"""Phase F.5 — dynamic confidence with multi-factor synergy."""

from __future__ import annotations

from typing import Any


def compute_dynamic_confidence(
    base_confidence: float,
    *,
    consecutive_bars: int = 0,
    volume_multiple: float = 0.0,
    funding_negative: bool = False,
    oi_stalled: bool = False,
    demand_zone: bool = False,
    historical_reversal_rate: float = 0.0,
    trend_stage: str = "",
    liquidation_signal: bool = False,
    support_lost: bool = False,
) -> tuple[float, dict[str, float], list[str]]:
    """Return (score 0–10, component bonuses, active factor labels)."""
    bonuses: dict[str, float] = {}
    active: list[str] = []

    if consecutive_bars >= 10:
        bonuses["trend_streak"] = min(1.2, 0.04 * consecutive_bars)
        active.append("trend")
    elif consecutive_bars >= 5:
        bonuses["trend_streak"] = 0.35
        active.append("trend")

    if volume_multiple >= 3.0:
        bonuses["volume_spike"] = min(1.0, 0.15 * volume_multiple)
        active.append("volume")
    elif volume_multiple >= 1.8:
        bonuses["volume_spike"] = 0.25
        active.append("volume")

    if funding_negative:
        bonuses["funding_flip"] = 0.55
        active.append("funding")

    if oi_stalled:
        bonuses["oi_stall"] = 0.45
        active.append("oi")

    if demand_zone:
        bonuses["demand_zone"] = 0.5
        active.append("demand")

    if historical_reversal_rate >= 0.65:
        bonuses["history"] = min(0.9, historical_reversal_rate)
        active.append("history")

    if trend_stage in ("Capitulation", "Reversal Candidate"):
        bonuses["trend_stage"] = 0.4
        active.append("capitulation")

    if liquidation_signal:
        bonuses["liquidations"] = 0.35
        active.append("liquidations")

    if support_lost:
        bonuses["structure"] = 0.3
        active.append("structure")

    synergy = max(0, len(active) - 2) * 0.35
    if synergy:
        bonuses["synergy"] = round(synergy, 2)

    total_bonus = sum(bonuses.values())
    score = round(min(10.0, max(0.0, base_confidence + total_bonus)), 1)
    return score, bonuses, active
