"""Market behavior research configuration."""

from __future__ import annotations

# Seconds-before-close buckets for late-window dynamics.
LATE_WINDOW_BUCKETS: tuple[int, ...] = (60, 45, 30, 15, 5)

# Take-profit levels (quote price, 0–1) tested after hypothetical entry.
TP_LEVELS: tuple[float, ...] = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85)

# Minimum observations per market to include in analysis.
MIN_OBS_PER_MARKET: int = 20

# Minimum seconds_left at entry for forward TP reach measurement.
MIN_SECONDS_LEFT_FOR_TP: int = 30

# Entry price bucket width (e.g. 0.05 → 45–50c).
ENTRY_BUCKET_WIDTH: float = 0.05
