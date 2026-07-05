"""15m bidirectional research configuration — independent from 5m execution."""

from __future__ import annotations

TF_15M_SECONDS = 900

# Minimum samples (15m-specific, not 5m thresholds)
MIN_MARKETS = 20
MIN_TRADES_VERDICT = 40
MIN_TRADES_FAMILY = 15
MIN_BOOTSTRAP_N = 20

# Walk-forward
WF_TRAIN_RATIO = 0.70
WF_FOLDS = 4
BOOTSTRAP_SAMPLES = 1000

# Stress slippage on entry (absolute probability points)
STRESS_LEVELS = (0.01, 0.02)

# Default exit research parameters (15m-calibrated starting points)
FIXED_TP_PCT = 12.0
FIXED_SL_PCT = 8.0
TRAIL_ACTIVATE_PCT = 6.0
TRAIL_DISTANCE_PCT = 4.0
TIME_EXIT_SECONDS_LEFT = 45

# Family threshold keys — calibrated on train fold only
DEFAULT_MOVE_THRESHOLDS = {
    "early_1m_usd": 30.0,
    "delayed_3m_usd": 50.0,
    "impulse_1m_usd": 60.0,
    "pullback_pct": 0.15,
    "late_5m_usd": 40.0,
    "divergence_prob": 0.08,
    "recross_usd": 5.0,
}

# Entry windows (seconds from 15m open) — researched, not copied from 5m
ENTRY_WINDOWS = {
    "early_momentum": (30, 180),
    "delayed_momentum": (180, 480),
    "pullback_continuation": (120, 420),
    "reversal_impulse": (60, 300),
    "strike_recross": (30, 600),
    "late_confirmation": (540, 780),
    "prob_spot_divergence": (60, 720),
}

VERDICTS = (
    "NO_EDGE",
    "COLLECT_MORE_DATA",
    "CONTINUE_RESEARCH",
    "READY_FOR_15M_SHADOW",
)
