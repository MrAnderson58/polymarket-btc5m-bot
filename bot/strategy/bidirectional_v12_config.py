"""Bidirectional Momentum V1.2 shadow config — parallel to V1.1.

Filters derived from counterfactual research (robustness-first, not max PF).
Update V12_CANDIDATE_SPEC after running:
  python -m bot.research.bidirectional_v12_counterfactual

Does NOT replace V1.1 shadow.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.strategy.bidirectional_momentum import EntryConfig

# Shared with counterfactual research — single source of truth for V1.2 filters.


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    sfs_min: int = 15
    sfs_max: int = 250
    exclude_sfs_lo: int | None = None
    exclude_sfs_hi: int | None = None
    yes_exclude_035_040: bool = False
    yes_exclude_below_025: bool = False
    yes_min_ask: float | None = None
    yes_allowed_lo: float | None = None
    yes_allowed_hi: float | None = None
    yes_min_move: float = 5.0
    no_min_move: float = 5.0
    exclude_yes_move_lo: float | None = None
    exclude_yes_move_hi: float | None = None
    allowed_regimes: tuple[str, ...] | None = None
    skip_regimes: tuple[str, ...] = ("CHOP", "REVERSAL")


# Default: combo_robust_core from counterfactual grid (update after production research run)
V12_CANDIDATE_SPEC = CandidateSpec(
    name="combo_robust_core",
    sfs_max=180,
    yes_exclude_035_040=True,
    yes_min_move=10.0,
    no_min_move=5.0,
    allowed_regimes=("NORMAL", "MOMENTUM"),
)

SHADOW_V12_ENTRY_CONFIG = EntryConfig(
    min_confidence=0.55,
    min_move_30s=5.0,
    yes_max_ask=0.45,
    no_max_ask=0.45,
    no_avoid_zone_lo=0.28,
    no_avoid_zone_hi=0.35,
    max_spread=0.06,
    min_consistency=0.5,
    min_seconds_from_start=V12_CANDIDATE_SPEC.sfs_min,
    max_seconds_from_start=V12_CANDIDATE_SPEC.sfs_max,
    skip_regimes=("CHOP", "REVERSAL"),
)


def passes_v12_filters(
    *,
    side: str,
    entry_price: float,
    btc_move_30s: float | None,
    seconds_from_start: int,
    regime: str,
    spec: CandidateSpec | None = None,
) -> bool:
    """Post-direction V1.2 filters (no future data)."""
    spec = spec or V12_CANDIDATE_SPEC

    if seconds_from_start < spec.sfs_min or seconds_from_start > spec.sfs_max:
        return False
    if spec.exclude_sfs_lo is not None and spec.exclude_sfs_hi is not None:
        if spec.exclude_sfs_lo <= seconds_from_start < spec.exclude_sfs_hi:
            return False

    if spec.allowed_regimes:
        if regime not in spec.allowed_regimes:
            return False
    elif regime in spec.skip_regimes:
        return False

    move = btc_move_30s
    if side == "YES":
        if move is not None and move < spec.yes_min_move:
            return False
        if move is not None and spec.exclude_yes_move_lo is not None:
            if spec.exclude_yes_move_lo <= abs(move) < (spec.exclude_yes_move_hi or 999):
                return False
        if spec.yes_exclude_035_040 and 0.35 <= entry_price < 0.40:
            return False
        if spec.yes_exclude_below_025 and entry_price < 0.25:
            return False
        if spec.yes_min_ask is not None and entry_price < spec.yes_min_ask:
            return False
        if spec.yes_allowed_lo is not None and entry_price < spec.yes_allowed_lo:
            return False
        if spec.yes_allowed_hi is not None and entry_price > spec.yes_allowed_hi:
            return False
    elif side == "NO":
        if move is not None and abs(move) < spec.no_min_move:
            return False

    return True
