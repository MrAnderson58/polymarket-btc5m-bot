"""Daily research configuration — independent from 5m/15m/1h."""

from __future__ import annotations

MIN_MARKETS = 10
MIN_TRADES_FAMILY = 10
MIN_TRADES_VERDICT = 30
WF_TRAIN_RATIO = 0.70

FIXED_TP_PCT = 18.0
FIXED_SL_PCT = 12.0
TRAIL_ACTIVATE_PCT = 10.0
TRAIL_DISTANCE_PCT = 6.0
TIME_EXIT_SECONDS_LEFT = 3600

DEFAULT_THRESHOLDS = {
    "displacement_usd": 200.0,
    "reversal_usd": 150.0,
    "divergence_prob": 0.08,
    "open_dist_pct": 0.25,
    "high_low_dist_pct": 0.4,
    "vol_regime_ratio": 1.4,
    "intraday_trend_usd": 180.0,
}

ENTRY_WINDOWS = {
    "session_asia": (0, 28800),
    "session_europe": (28800, 57600),
    "session_us": (57600, 82800),
    "open_distance": (3600, 43200),
    "rolling_extreme": (7200, 64800),
    "vol_regime": (3600, 72000),
    "intraday_trend": (14400, 72000),
    "reversal_displacement": (18000, 75000),
    "prob_divergence": (7200, 80000),
    "time_to_expiry": (3600, 14400),
}
