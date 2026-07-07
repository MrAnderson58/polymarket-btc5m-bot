"""Causal exit models for archetype simulation (ask entry, bid/settlement exit)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExitModel(str, Enum):
    FIXED_TP = "fixed_tp"
    TIME_EXIT = "time_exit"
    SETTLEMENT = "settlement"
    TRAILING = "trailing"


@dataclass(frozen=True)
class ExitSpec:
    model: ExitModel
    tp: float | None = None
    time_exit_seconds: int | None = None
    trailing_stop: float | None = None

    def label(self) -> str:
        if self.model == ExitModel.FIXED_TP:
            return f"tp={self.tp:.2f}"
        if self.model == ExitModel.TIME_EXIT:
            return f"time={self.time_exit_seconds}s"
        if self.model == ExitModel.SETTLEMENT:
            return "settlement"
        if self.model == ExitModel.TRAILING:
            return f"trail={self.trailing_stop:.2f}"
        return self.model.value


def forward_exit(
    observations: list[dict],
    start_idx: int,
    *,
    direction: str,
    entry_price: float,
    spec: ExitSpec,
) -> tuple[float, int, bool]:
    """Replay exit using only future observations (no look-ahead)."""
    bid_key = "yes_bid" if direction == "YES" else "no_bid"
    entry_ts = int(observations[start_idx]["timestamp"])

    if spec.model == ExitModel.SETTLEMENT:
        return _settlement_exit(observations, direction, entry_price)

    peak_bid = entry_price
    exit_price = float(observations[start_idx].get(bid_key) or entry_price)
    exit_ts = entry_ts
    won = False

    for obs in observations[start_idx + 1:]:
        bid = obs.get(bid_key)
        if bid is None:
            continue
        bid_f = float(bid)
        ts = int(obs["timestamp"])
        exit_price = bid_f
        exit_ts = ts

        if spec.model == ExitModel.FIXED_TP and spec.tp is not None:
            if bid_f >= spec.tp:
                return spec.tp, ts, True

        elif spec.model == ExitModel.TIME_EXIT and spec.time_exit_seconds is not None:
            if ts - entry_ts >= spec.time_exit_seconds:
                return bid_f, ts, bid_f > entry_price

        elif spec.model == ExitModel.TRAILING and spec.trailing_stop is not None:
            peak_bid = max(peak_bid, bid_f)
            if peak_bid - bid_f >= spec.trailing_stop:
                return bid_f, ts, bid_f > entry_price

    if spec.model == ExitModel.FIXED_TP and spec.tp is not None:
        won = exit_price >= spec.tp
    elif spec.model == ExitModel.TIME_EXIT:
        won = exit_price > entry_price
    elif spec.model == ExitModel.TRAILING:
        won = exit_price > entry_price

    return exit_price, exit_ts, won


def _settlement_exit(
    observations: list[dict],
    direction: str,
    entry_price: float,
) -> tuple[float, int, bool]:
    """Exit at market resolution using final BTC vs strike."""
    last = observations[-1]
    exit_ts = int(last["timestamp"])
    strike = last.get("strike")
    if strike is None:
        for obs in reversed(observations):
            if obs.get("strike") is not None:
                strike = obs["strike"]
                break
    if strike is None:
        bid_key = "yes_bid" if direction == "YES" else "no_bid"
        exit_price = float(last.get(bid_key) or entry_price)
        return exit_price, exit_ts, exit_price > entry_price

    btc = float(last["btc_price"])
    yes_wins = btc >= float(strike)
    won = yes_wins if direction == "YES" else not yes_wins
    exit_price = 1.0 if won else 0.0
    return exit_price, exit_ts, won


def build_exit_outcomes(
    observations: list[dict],
    start_idx: int,
    *,
    direction: str,
    entry_price: float,
    specs: tuple[ExitSpec, ...],
) -> dict[str, tuple[float, int, bool]]:
    """Precompute exits for multiple ExitSpec keys."""
    outcomes: dict[str, tuple[float, int, bool]] = {}
    for spec in specs:
        outcomes[spec.label()] = forward_exit(
            observations,
            start_idx,
            direction=direction,
            entry_price=entry_price,
            spec=spec,
        )
    return outcomes
