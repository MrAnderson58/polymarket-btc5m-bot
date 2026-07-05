"""Separate timeframe research scaffolds — 15m / 1h / daily."""

from __future__ import annotations

from typing import Any


TIMEFRAME_RESEARCH_DIMENSIONS = [
    "entry_timing",
    "movement_horizon",
    "distance_to_strike",
    "token_ask",
    "spread",
    "volatility_regime",
    "momentum_vs_reversal",
    "exit_logic",
    "time_remaining",
    "yes_no_asymmetry",
]


def timeframe_research_status() -> dict[str, Any]:
    """Scaffold status for separate HTF strategy research (not 5m params)."""
    return {
        "15m": {
            "status": "SCAFFOLD",
            "note": "Requires multi_timeframe_snapshots + dedicated replay on 15m obs",
            "dimensions": TIMEFRAME_RESEARCH_DIMENSIONS,
            "param_inheritance": "FORBIDDEN — do not copy 5m EntryConfig directly",
        },
        "1h": {
            "status": "SCAFFOLD",
            "note": "Slug discovery via Gamma search; ET-based windows",
            "dimensions": TIMEFRAME_RESEARCH_DIMENSIONS,
            "param_inheritance": "FORBIDDEN",
        },
        "daily": {
            "status": "SCAFFOLD",
            "note": "Daily markets use noon ET Binance resolution; separate research track",
            "dimensions": TIMEFRAME_RESEARCH_DIMENSIONS,
            "param_inheritance": "FORBIDDEN",
        },
    }
