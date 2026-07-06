"""Strategy simulator configuration."""

from __future__ import annotations

MIN_OBS_PER_MARKET: int = 20
MIN_TRADES_FOR_RANK: int = 30
DEFAULT_TP: float = 0.60

# Discovery grids (observe-only brute force).
DISCOVER_MAX_ENTRIES: tuple[float, ...] = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
DISCOVER_MIN_DELTAS: tuple[float | None, ...] = (None, -60, -25, 0, 25, 50)
DISCOVER_MAX_DELTAS: tuple[float | None, ...] = (None, -60, -25, 0, 25, 50)
DISCOVER_MAX_SPREADS: tuple[float, ...] = (0.01, 0.02, 0.03, 0.05)
DISCOVER_MIN_SECONDS: tuple[int, ...] = (45, 60, 90, 120, 180)
DISCOVER_TPS: tuple[float, ...] = (0.55, 0.60, 0.65, 0.70)
DISCOVER_DIRECTIONS: tuple[str, ...] = ("YES", "NO")

# BTC velocity / delta lookback windows (seconds).
BTC_VELOCITY_WINDOWS: tuple[int, ...] = (5, 10, 20, 30)
SPREAD_LOOKBACK_WINDOWS: tuple[int, ...] = (5, 10, 20)
