"""Trading AI Strategy Review v1 — observe-only constants."""

from __future__ import annotations

REVIEW_VERSION = "1.0"

ENTRY_LEVELS = [round(x * 0.01, 2) for x in range(34, 41)]
STOP_ALTERNATIVES = (-10.0, -12.0, -15.0, -18.0, -20.0, -22.0, -25.0)
TRAIL_ACTIVATION_OPTS = (0.02, 0.03, 0.04, 0.05)
TRAIL_DISTANCE_OPTS = (0.01, 0.015, 0.02, 0.03)
RECOVERY_WINDOWS = (15, 30, 45, 60, "expiry")

MIN_TRADES_SINCE_CHANGE = 300
TARGET_TRADES_SINCE_CHANGE = 500
MIN_CONFIDENCE_PCT = 80.0
MAX_P_VALUE = 0.05

SAFETY_GATE = {
    "min_trades_since_change": MIN_TRADES_SINCE_CHANGE,
    "target_trades_since_change": TARGET_TRADES_SINCE_CHANGE,
    "require_walk_forward_pass": True,
    "max_overfit_risk": "MEDIUM",
    "min_confidence_pct": MIN_CONFIDENCE_PCT,
    "max_p_value": MAX_P_VALUE,
}
