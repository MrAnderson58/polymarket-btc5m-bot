"""Fixed position sizing tiers (live safety — no dynamic scaling)."""

from __future__ import annotations

from enum import Enum

from bot.config import LIVE_MODE


class PositionTier(str, Enum):
    MICRO = "micro"
    SMALL = "small"
    NORMAL = "normal"


TIER_USDC: dict[PositionTier, float] = {
    PositionTier.MICRO: 1.0,
    PositionTier.SMALL: 5.0,
    PositionTier.NORMAL: 10.0,
}

# Safety: live v1 allows MICRO only ($1).
ALLOWED_LIVE_TIERS = frozenset({PositionTier.MICRO})


def current_tier() -> PositionTier:
    raw = (LIVE_MODE or "micro").strip().lower()
    try:
        return PositionTier(raw)
    except ValueError:
        return PositionTier.MICRO


def tier_usdc(tier: PositionTier) -> float:
    return TIER_USDC[tier]


def max_allowed_usdc() -> float:
    """Hard cap — increasing size is forbidden in live v1."""
    tier = current_tier()
    if tier not in ALLOWED_LIVE_TIERS:
        return TIER_USDC[PositionTier.MICRO]
    return TIER_USDC[tier]


def effective_position_size_usdc() -> float:
    """Effective stake for live micro mode (always capped at max_allowed_usdc)."""
    return min(max_allowed_usdc(), TIER_USDC.get(current_tier(), 1.0))
