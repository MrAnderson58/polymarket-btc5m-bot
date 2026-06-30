"""Trend detection for V4 Shadow observe phase."""

from __future__ import annotations

from dataclasses import dataclass

from bot.v4.observe import ObservationSnapshot

Side = str  # "YES" | "NO"


@dataclass(frozen=True)
class TrendSignal:
    side: Side
    delta_change: float
    ask_change: float
    consistency: float


def detect_trend(history: list[ObservationSnapshot]) -> TrendSignal | None:
    """
    Detect directional trend from recent observations.

    Requires at least 10 samples with valid delta and side quotes.
    """
    valid = [
        obs
        for obs in history
        if obs.delta is not None and obs.yes_ask is not None and obs.no_ask is not None
    ]
    if len(valid) < 10:
        return None

    first, last = valid[0], valid[-1]
    delta_change = last.delta - first.delta  # type: ignore[operator]

    yes_ask_change = last.yes_ask - first.yes_ask  # type: ignore[operator]
    no_ask_change = last.no_ask - first.no_ask  # type: ignore[operator]

    if abs(delta_change) < 15:
        return None

    if delta_change > 0 and yes_ask_change >= 0:
        side: Side = "YES"
        ask_change = yes_ask_change
    elif delta_change < 0 and no_ask_change >= 0:
        side = "NO"
        ask_change = no_ask_change
    else:
        return None

    moves = 0
    aligned = 0
    for prev, curr in zip(valid, valid[1:]):
        step = curr.delta - prev.delta  # type: ignore[operator]
        if abs(step) < 0.01:
            continue
        moves += 1
        if side == "YES" and step > 0:
            aligned += 1
        elif side == "NO" and step < 0:
            aligned += 1

    consistency = aligned / moves if moves else 0.0
    if consistency < 0.55:
        return None

    return TrendSignal(
        side=side,
        delta_change=delta_change,
        ask_change=ask_change,
        consistency=consistency,
    )
