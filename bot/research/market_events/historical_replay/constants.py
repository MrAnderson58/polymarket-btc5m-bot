"""Fixed replay versions and strategy definitions — not optimized on OOS."""

from __future__ import annotations

REPLAY_DETECTOR_VERSION = "e4_production_v1"
REPLAY_PROFILE_VERSION = "e31_descriptive_v1"
REPLAY_DATA_SOURCE = "HISTORICAL_REPLAY"
REPLAY_RUN_TAG_DEFAULT = "e4_default"

PATH_HORIZONS_SEC = (5, 15, 30, 60, 120, 180, 300, 600, 900, 1800, 3600)
CONTEXT_WINDOWS_SEC = (30 * 60, 2 * 3600, 6 * 3600, 24 * 3600)

THRESHOLD_GRID_PCT = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)
THRESHOLD_WINDOWS_SEC = (30, 60, 180)

# User strategy families S1–S5 (fixed params, versioned before evaluation).
STRATEGY_S1_FAST_TP = {
    "id": "S1_FAST_TP",
    "tp_pct": 0.5,
    "stop_pct": 1.0,
    "delayed_confirm": False,
}
STRATEGY_S2_TP_TO_BE = {
    "id": "S2_TP_TO_BE",
    "tp_pct": 0.5,
    "stop_pct": 1.0,
    "be_trigger_pct": 0.3,
    "delayed_confirm": False,
}
STRATEGY_S3_PARTIAL_BE_RUNNER = {
    "id": "S3_PARTIAL_BE_RUNNER",
    "tp1_pct": 0.5,
    "partial_frac": 0.5,
    "stop_pct": 1.0,
    "be_trigger_pct": 0.3,
    "trail_pct": 0.5,
    "delayed_confirm": False,
}
STRATEGY_S4_DELAYED_CONFIRMATION_TRAIL = {
    "id": "S4_DELAYED_CONFIRMATION_TRAIL",
    "stop_pct": 1.0,
    "trail_pct": 0.6,
    "delayed_confirm": True,
    "confirm_reclaim_pct": 0.50,
}
STRATEGY_S5_VOLATILITY_ADAPTIVE_TRAIL = {
    "id": "S5_VOLATILITY_ADAPTIVE_TRAIL",
    "stop_pct": 1.0,
    "vol_trail_mult": 1.5,
    "min_trail_pct": 0.3,
    "max_trail_pct": 1.2,
    "delayed_confirm": False,
}

REPLAY_STRATEGIES = (
    STRATEGY_S1_FAST_TP,
    STRATEGY_S2_TP_TO_BE,
    STRATEGY_S3_PARTIAL_BE_RUNNER,
    STRATEGY_S4_DELAYED_CONFIRMATION_TRAIL,
    STRATEGY_S5_VOLATILITY_ADAPTIVE_TRAIL,
)

SPLIT_TRAIN_RATIO = 0.60
SPLIT_VAL_RATIO = 0.20
SPLIT_HOLDOUT_RATIO = 0.20

FEE_BPS_SCENARIOS = (0.0, 10.0)
SLIPPAGE_BPS_SCENARIOS = (0.0, 10.0)
