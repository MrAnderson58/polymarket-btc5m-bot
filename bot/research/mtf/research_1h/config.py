"""1h research configuration — independent from 5m/15m."""

from __future__ import annotations

TF_1H_SECONDS = 3600

MIN_MARKETS = 15
MIN_TRADES_FAMILY = 12
MIN_TRADES_VERDICT = 35
WF_TRAIN_RATIO = 0.70

FIXED_TP_PCT = 15.0
FIXED_SL_PCT = 10.0
TRAIL_ACTIVATE_PCT = 8.0
TRAIL_DISTANCE_PCT = 5.0
TIME_EXIT_SECONDS_LEFT = 120

DEFAULT_THRESHOLDS = {
    "move_1m_usd": 40.0,
    "move_5m_usd": 80.0,
    "move_10m_usd": 120.0,
    "move_15m_usd": 150.0,
    "vol_expansion_ratio": 1.35,
    "divergence_prob": 0.07,
    "prob_lag_usd": 50.0,
    "failed_breakout_usd": 70.0,
}

ENTRY_WINDOWS = {
    "early_move_1m": (60, 300),
    "early_move_5m": (180, 600),
    "momentum_continue": (300, 1800),
    "vol_expansion": (120, 1200),
    "failed_breakout": (300, 1500),
    "prob_lag": (180, 900),
    "prob_divergence": (300, 2400),
    "late_time_remaining": (600, 3000),
}
