"""Discovery grid generation."""

from __future__ import annotations

from bot.research.strategy_simulator.config import (
    DISCOVER_DIRECTIONS,
    DISCOVER_MAX_DELTAS,
    DISCOVER_MAX_ENTRIES,
    DISCOVER_MAX_SPREADS,
    DISCOVER_MIN_DELTAS,
    DISCOVER_MIN_SECONDS,
    DISCOVER_TPS,
)
from bot.research.strategy_simulator.strategies import Strategy


def generate_discovery_grid() -> list[Strategy]:
    strategies: list[Strategy] = []
    for direction in DISCOVER_DIRECTIONS:
        for max_entry in DISCOVER_MAX_ENTRIES:
            for min_delta in DISCOVER_MIN_DELTAS:
                for max_delta in DISCOVER_MAX_DELTAS:
                    for max_spread in DISCOVER_MAX_SPREADS:
                        for min_sec in DISCOVER_MIN_SECONDS:
                            for tp in DISCOVER_TPS:
                                if tp <= max_entry:
                                    continue
                                if not Strategy.is_valid(
                                    direction=direction,
                                    max_entry=max_entry,
                                    min_delta=min_delta,
                                    max_delta=max_delta,
                                    max_spread=max_spread,
                                    min_seconds_left=min_sec,
                                    tp=tp,
                                ):
                                    continue
                                strategies.append(Strategy(
                                    direction=direction,
                                    max_entry=max_entry,
                                    min_delta=min_delta,
                                    max_delta=max_delta,
                                    max_spread=max_spread,
                                    min_seconds_left=min_sec,
                                    tp=tp,
                                ))
    return strategies
