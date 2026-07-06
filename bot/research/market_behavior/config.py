"""Market behavior research configuration."""

from __future__ import annotations

# Seconds-before-close buckets for late-window dynamics (legacy summary).
LATE_WINDOW_BUCKETS: tuple[int, ...] = (60, 45, 30, 15, 5)

# Take-profit levels for legacy mb_tp_probability table.
TP_LEVELS: tuple[float, ...] = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)

# Edge statistics TP levels (stored as TP55..TP75).
EDGE_TP_LEVELS: tuple[float, ...] = (0.55, 0.60, 0.65, 0.70, 0.75)

# Minimum observations per market to include in analysis.
MIN_OBS_PER_MARKET: int = 20

# Minimum seconds_left at entry for forward TP / edge measurement.
MIN_SECONDS_LEFT_FOR_TP: int = 30
MIN_SECONDS_LEFT_FOR_EDGE: int = 5

# Entry price bucket width (e.g. 0.05 → 45–50c).
ENTRY_BUCKET_WIDTH: float = 0.05

# BTC delta vs strike buckets (USD). Values define interval edges.
BTC_DELTA_BUCKET_EDGES: tuple[float, ...] = (
    -150, -100, -75, -50, -25, 0, 25, 50, 75, 100, 150,
)

# Seconds-left upper-bound thresholds (ascending).
SECONDS_LEFT_THRESHOLDS: tuple[int, ...] = (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240)

# Spread buckets in cents (ask - bid).
SPREAD_BUCKET_EDGES_CENTS: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)

# Minimum samples to surface an edge in reports.
MIN_EDGE_SAMPLES: int = 30

# Primary TP for EV ranking in edge-report.
PRIMARY_EV_TP: float = 0.60
