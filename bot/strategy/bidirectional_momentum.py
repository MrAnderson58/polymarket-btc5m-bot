"""Bidirectional Momentum Strategy V1 — Direction Engine.

Independently calculates P(YES) and P(NO) based on:
- BTC movement magnitude, velocity, acceleration
- Distance from strike
- Direction consistency
- Token pricing (ask)
- Spread quality
- Time remaining
- Regime classification

Returns structured decision without side bias.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from bot.research.features import MovementFeatures


@dataclass
class DirectionDecision:
    decision: str  # YES | NO | SKIP
    confidence: float
    probability_yes: float
    probability_no: float
    regime: str
    reason: str
    features: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EntryConfig:
    """Configuration for entry decisions — derived from research.

    V1.1 defaults are validated OOS on P3 (high-vol):
    - P3 PF=2.39, P(PF>1)=0.997, MaxCL=5
    - Both sides individually profitable OOS
    """
    # Entry thresholds by side
    yes_max_ask: float = 0.45
    no_max_ask: float = 0.45

    # NO avoid zone: 0.28-0.35 confirmed PF=0.30 OOS, P(PF>1)=0.054
    no_avoid_zone_lo: float = 0.28
    no_avoid_zone_hi: float = 0.35

    # Minimum movement to consider direction signal
    min_move_30s: float = 5.0

    # Minimum confidence to enter
    min_confidence: float = 0.55

    # Regime filters (V1.1: skip REVERSAL — P3 PF +0.27 improvement)
    skip_regimes: tuple[str, ...] = ("CHOP", "REVERSAL")

    # Time constraints (seconds from start)
    min_seconds_from_start: int = 15
    max_seconds_from_start: int = 250

    # Spread filter
    max_spread: float = 0.06

    # Direction consistency minimum
    min_consistency: float = 0.5


@dataclass
class ExitConfig:
    """Dynamic exit configuration — regime-dependent."""
    stop_loss_pct: float = -10.0
    trailing_activation_pct: float = 8.0
    trailing_distance_pct: float = 5.0
    time_stop_seconds: int = 90


# Default regime-specific exit profiles (to be validated by replay)
REGIME_EXIT_PROFILES: dict[str, ExitConfig] = {
    "NORMAL": ExitConfig(
        stop_loss_pct=-10.0,
        trailing_activation_pct=8.0,
        trailing_distance_pct=5.0,
        time_stop_seconds=90,
    ),
    "MOMENTUM": ExitConfig(
        stop_loss_pct=-12.0,
        trailing_activation_pct=10.0,
        trailing_distance_pct=5.0,
        time_stop_seconds=90,
    ),
    "STRONG_MOMENTUM": ExitConfig(
        stop_loss_pct=-15.0,
        trailing_activation_pct=12.0,
        trailing_distance_pct=7.0,
        time_stop_seconds=120,
    ),
    "NEWS_SPIKE": ExitConfig(
        stop_loss_pct=-15.0,
        trailing_activation_pct=15.0,
        trailing_distance_pct=7.0,
        time_stop_seconds=120,
    ),
    "REVERSAL": ExitConfig(
        stop_loss_pct=-7.0,
        trailing_activation_pct=5.0,
        trailing_distance_pct=3.0,
        time_stop_seconds=60,
    ),
    "CHOP": ExitConfig(
        stop_loss_pct=-5.0,
        trailing_activation_pct=5.0,
        trailing_distance_pct=3.0,
        time_stop_seconds=45,
    ),
}


def evaluate_direction(
    feat: MovementFeatures,
    config: EntryConfig | None = None,
) -> DirectionDecision:
    """Core direction evaluation — symmetric, no side bias.

    Calculates independent P(YES) and P(NO) from features available at
    the decision timestamp.
    """
    cfg = config or EntryConfig()

    features_dict = {
        "btc_move_30s": feat.btc_move_30s,
        "btc_velocity_10s": feat.btc_velocity_10s,
        "btc_acceleration": feat.btc_acceleration,
        "distance_from_strike_pct": feat.distance_from_strike_pct,
        "direction_consistency": feat.direction_consistency,
        "spread": feat.spread,
        "seconds_left": feat.seconds_left,
        "regime": feat.regime,
        "yes_ask": feat.yes_ask,
        "no_ask": feat.no_ask,
    }

    # Time filter
    if feat.seconds_from_start < cfg.min_seconds_from_start:
        return DirectionDecision(
            decision="SKIP", confidence=0.0,
            probability_yes=0.0, probability_no=0.0,
            regime=feat.regime, reason="too_early",
            features=features_dict,
        )

    if feat.seconds_from_start > cfg.max_seconds_from_start:
        return DirectionDecision(
            decision="SKIP", confidence=0.0,
            probability_yes=0.0, probability_no=0.0,
            regime=feat.regime, reason="too_late",
            features=features_dict,
        )

    # Regime filter
    if feat.regime in cfg.skip_regimes:
        return DirectionDecision(
            decision="SKIP", confidence=0.0,
            probability_yes=0.0, probability_no=0.0,
            regime=feat.regime, reason=f"regime_{feat.regime}",
            features=features_dict,
        )

    # Spread filter
    if feat.spread > cfg.max_spread:
        return DirectionDecision(
            decision="SKIP", confidence=0.0,
            probability_yes=0.0, probability_no=0.0,
            regime=feat.regime, reason="spread_too_wide",
            features=features_dict,
        )

    # Compute directional probabilities
    p_yes = _compute_p_yes(feat, cfg)
    p_no = _compute_p_no(feat, cfg)

    # Normalize (they don't need to sum to 1 — SKIP absorbs remainder)
    best_side = "YES" if p_yes > p_no else "NO"
    best_p = max(p_yes, p_no)

    # Confidence threshold
    if best_p < cfg.min_confidence:
        return DirectionDecision(
            decision="SKIP", confidence=best_p,
            probability_yes=p_yes, probability_no=p_no,
            regime=feat.regime, reason="low_confidence",
            features=features_dict,
        )

    # Price filter
    if best_side == "YES" and feat.yes_ask > cfg.yes_max_ask:
        return DirectionDecision(
            decision="SKIP", confidence=best_p,
            probability_yes=p_yes, probability_no=p_no,
            regime=feat.regime, reason="yes_ask_too_expensive",
            features=features_dict,
        )
    if best_side == "NO" and feat.no_ask > cfg.no_max_ask:
        return DirectionDecision(
            decision="SKIP", confidence=best_p,
            probability_yes=p_yes, probability_no=p_no,
            regime=feat.regime, reason="no_ask_too_expensive",
            features=features_dict,
        )
    if best_side == "NO" and cfg.no_avoid_zone_lo <= feat.no_ask < cfg.no_avoid_zone_hi:
        return DirectionDecision(
            decision="SKIP", confidence=best_p,
            probability_yes=p_yes, probability_no=p_no,
            regime=feat.regime, reason="no_ask_avoid_zone",
            features=features_dict,
        )

    reason = _build_reason(feat, best_side)
    return DirectionDecision(
        decision=best_side,
        confidence=best_p,
        probability_yes=p_yes,
        probability_no=p_no,
        regime=feat.regime,
        reason=reason,
        features=features_dict,
    )


def get_exit_config(regime: str) -> ExitConfig:
    """Get exit configuration for the given regime."""
    return REGIME_EXIT_PROFILES.get(regime, REGIME_EXIT_PROFILES["NORMAL"])


def _compute_p_yes(feat: MovementFeatures, cfg: EntryConfig) -> float:
    """Compute probability that YES is the correct side.

    Positive BTC movement → potential YES momentum (BTC going up → YES wins).
    """
    score = 0.5  # baseline

    # Movement signal (positive = YES direction)
    move_30 = feat.btc_move_30s
    if move_30 > cfg.min_move_30s:
        score += min(0.2, move_30 / 200.0)
    elif move_30 < -cfg.min_move_30s:
        score -= min(0.15, abs(move_30) / 250.0)

    # Velocity confirmation
    if feat.btc_velocity_10s > 0 and move_30 > 0:
        score += min(0.1, feat.btc_velocity_10s / 5.0)

    # Acceleration (momentum building)
    if feat.btc_acceleration > 0 and move_30 > 0:
        score += min(0.05, feat.btc_acceleration * 10)

    # Distance from strike: BTC above strike favors YES
    if feat.distance_from_strike_pct > 0:
        score += min(0.1, feat.distance_from_strike_pct / 2.0)
    elif feat.distance_from_strike_pct < -0.1:
        score -= min(0.1, abs(feat.distance_from_strike_pct) / 3.0)

    # Direction consistency
    if move_30 > 0 and feat.direction_consistency >= cfg.min_consistency:
        score += 0.05 * feat.direction_consistency

    # Time value: more time left → more uncertainty
    if feat.seconds_left < 60:
        score += 0.03 if feat.distance_from_strike_pct > 0 else -0.02

    return max(0.0, min(1.0, score))


def _compute_p_no(feat: MovementFeatures, cfg: EntryConfig) -> float:
    """Compute probability that NO is the correct side.

    Negative BTC movement → potential NO momentum (BTC going down → NO wins).
    """
    score = 0.5  # baseline

    # Movement signal (negative = NO direction)
    move_30 = feat.btc_move_30s
    if move_30 < -cfg.min_move_30s:
        score += min(0.2, abs(move_30) / 200.0)
    elif move_30 > cfg.min_move_30s:
        score -= min(0.15, move_30 / 250.0)

    # Velocity confirmation
    if feat.btc_velocity_10s < 0 and move_30 < 0:
        score += min(0.1, abs(feat.btc_velocity_10s) / 5.0)

    # Acceleration (momentum building for NO)
    if feat.btc_acceleration < 0 and move_30 < 0:
        score += min(0.05, abs(feat.btc_acceleration) * 10)

    # Distance from strike: BTC below strike favors NO
    if feat.distance_from_strike_pct < 0:
        score += min(0.1, abs(feat.distance_from_strike_pct) / 2.0)
    elif feat.distance_from_strike_pct > 0.1:
        score -= min(0.1, feat.distance_from_strike_pct / 3.0)

    # Direction consistency
    if move_30 < 0 and feat.direction_consistency >= cfg.min_consistency:
        score += 0.05 * feat.direction_consistency

    # Time value: less time + below strike → NO more likely to settle
    if feat.seconds_left < 60:
        score += 0.03 if feat.distance_from_strike_pct < 0 else -0.02

    return max(0.0, min(1.0, score))


def _build_reason(feat: MovementFeatures, side: str) -> str:
    parts = [f"{feat.regime.lower()}"]
    if side == "YES":
        parts.append(f"btc+{feat.btc_move_30s:.0f}")
    else:
        parts.append(f"btc{feat.btc_move_30s:.0f}")
    parts.append(f"v10={feat.btc_velocity_10s:.1f}")
    if feat.direction_consistency >= 0.8:
        parts.append("consistent")
    return "_".join(parts)
