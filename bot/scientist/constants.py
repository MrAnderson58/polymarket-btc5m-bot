"""Trading AI Scientist v1 — observe-only research constants."""

from __future__ import annotations

SOURCE_TABLE = "early_reversion_v2_trades"
SCIENTIST_VERSION = "1.0"

MIN_SAMPLE_TRADES = 300
TARGET_SAMPLE_TRADES = 500

EXPERIMENT_STATUSES = ("WAITING", "TESTING", "PASSED", "FAILED", "REJECTED")
PRIORITY_LEVELS = ("HIGH", "MEDIUM", "LOW")
RISK_LEVELS = ("HIGH", "MEDIUM", "LOW")

QUALITY_RULES = {
    "min_sample_for_recommendation": MIN_SAMPLE_TRADES,
    "target_sample": TARGET_SAMPLE_TRADES,
    "require_walk_forward": True,
    "max_overfit_risk": "MEDIUM",
    "min_p_value": 0.10,
}
