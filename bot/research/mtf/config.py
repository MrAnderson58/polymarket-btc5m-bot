"""Multi-timeframe research configuration — research/collector only."""

from __future__ import annotations

# Execution timeframe
TF_5M_SECONDS = 300

# Context timeframes
TF_15M_SECONDS = 900
TF_1H_SECONDS = 3600
TF_4H_SECONDS = 14400
TF_DAILY_SECONDS = 86400

# Polymarket slug patterns (discovery)
SLUG_5M_PREFIX = "btc-updown-5m"
SLUG_15M_PREFIX = "btc-updown-15m"
SLUG_1H_PREFIXES = ("bitcoin-up-or-down-", "btc-updown-1h", "btc-updown-60m")
SLUG_DAILY_PREFIXES = ("bitcoin-up-or-down-on-", "btc-updown-daily")

# BTC context lookbacks (seconds)
BTC_RETURN_WINDOWS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
}

# Label thresholds (% return)
HTF_STRONG_THRESHOLD = 0.35
HTF_MILD_THRESHOLD = 0.08

# Walk-forward
WF_TRAIN_RATIO = 0.70
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_MIN_N = 20

# Verdict thresholds
MIN_PM_SNAPSHOT_COVERAGE = 0.30
MIN_TRADES_FOR_VERDICT = 50
