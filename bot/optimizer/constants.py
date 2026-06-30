"""Grid constants for Trading AI Optimizer v1."""

from __future__ import annotations

ENTRY_GRID: tuple[float, ...] = (0.34, 0.35, 0.36, 0.37, 0.38, 0.39, 0.40)
STOP_GRID: tuple[float, ...] = (-10.0, -12.0, -14.0, -16.0, -18.0, -20.0, -22.0, -25.0)
TRAILING_ACTIVATION_GRID: tuple[float, ...] = (0.02, 0.025, 0.03, 0.035, 0.04, 0.045)
TRAILING_DISTANCE_GRID: tuple[float, ...] = (0.005, 0.01, 0.015, 0.02)
TIME_STOP_GRID: tuple[int, ...] = (45, 60, 75, 90, 120)
BTC_FILTER_GRID: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0, 25.0, 30.0)

SOURCE_TABLE = "early_reversion_v2_trades"
SETTLEMENT_BID = 0.99
CONFIDENCE_HIGH_N = 100
CONFIDENCE_MEDIUM_N = 30

FEATURE_COLUMNS: tuple[str, ...] = (
    "entry_price",
    "btc_move_5s",
    "btc_move_10s",
    "btc_move_15s",
    "btc_move_20s",
    "btc_move_30s",
    "btc_move_45s",
    "btc_move_60s",
    "btc_move_90s",
    "seconds_open",
    "spread",
    "distance_to_strike",
    "volatility_15s",
    "volatility_30s",
    "volatility_60s",
    "holding_time",
    "mfe",
    "mae",
)
