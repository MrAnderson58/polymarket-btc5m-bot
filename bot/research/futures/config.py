"""Futures signal research configuration."""

from __future__ import annotations

PARSER_VERSION = "deterministic_v1"

# Outcome evaluation horizons (seconds)
OUTCOME_HORIZONS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "12h": 43200,
    "24h": 86400,
}

# Price feature lookbacks (seconds)
RETURN_WINDOWS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "24h": 86400,
}

# Source scoring minimum sample sizes
MIN_SOURCE_N = 30
MIN_SYMBOL_N = 15
MIN_WALK_FORWARD_TRADES = 50

# Walk-forward
WF_TRAIN_RATIO = 0.70
WF_FOLDS = 5
BOOTSTRAP_SAMPLES = 1000

# Binance endpoints (research only)
BINANCE_SPOT_API = "https://api.binance.com"
BINANCE_FUTURES_API = "https://fapi.binance.com"

# Standardized research PnL when explicit levels missing (%)
STANDARDIZED_RISK_PCT = 1.0
STANDARDIZED_RR = 2.0

# Observe agent
OBSERVE_MODEL_VERSION = "observe_v1"
OBSERVE_FEATURE_VERSION = "features_v1"
