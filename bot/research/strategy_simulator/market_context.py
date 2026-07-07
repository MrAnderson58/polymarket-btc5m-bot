"""Precomputed market path index and cached snapshot features."""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.strategy_simulator.config import BTC_VELOCITY_WINDOWS, SPREAD_LOOKBACK_WINDOWS
from bot.research.strategy_simulator.features import SnapshotFeatures, _side_spread
from bot.research.strategy_simulator.path_index import find_idx_at_or_before


@dataclass(frozen=True)
class ForwardOutcomes:
    """Forward exit outcomes from an entry index for all TP levels."""

    by_tp: dict[float, tuple[float, int, bool]]  # tp -> (exit_price, exit_ts, won)

    def exit_for(self, tp: float) -> tuple[float, int, bool]:
        if tp in self.by_tp:
            return self.by_tp[tp]
        for level in sorted(self.by_tp):
            price, ts, won = self.by_tp[level]
            if won and price >= tp:
                return tp, ts, True
        return self.by_tp[max(self.by_tp)]


@dataclass
class MarketPathContext:
    """One-time preprocessing for a market observation path."""

    market_slug: str
    observations: list[dict]
    timestamps: list[int]
    strikes: list[float | None]
    features_yes: list[SnapshotFeatures | None]
    features_no: list[SnapshotFeatures | None]
    forward_yes: list[ForwardOutcomes]
    forward_no: list[ForwardOutcomes]

    @classmethod
    def build(
        cls,
        market_slug: str,
        observations: list[dict],
        *,
        tp_levels: tuple[float, ...],
    ) -> MarketPathContext:
        n = len(observations)
        timestamps = [int(o["timestamp"]) for o in observations]
        strikes = _precompute_strikes(observations)
        features_yes = [
            _build_features_at(observations, timestamps, strikes, i, side="YES")
            for i in range(n)
        ]
        features_no = [
            _build_features_at(observations, timestamps, strikes, i, side="NO")
            for i in range(n)
        ]
        forward_yes = [
            _build_forward_outcomes(observations, i, "YES", tp_levels)
            for i in range(n)
        ]
        forward_no = [
            _build_forward_outcomes(observations, i, "NO", tp_levels)
            for i in range(n)
        ]
        return cls(
            market_slug=market_slug,
            observations=observations,
            timestamps=timestamps,
            strikes=strikes,
            features_yes=features_yes,
            features_no=features_no,
            forward_yes=forward_yes,
            forward_no=forward_no,
        )


def _precompute_strikes(observations: list[dict]) -> list[float | None]:
    last: float | None = None
    strikes: list[float | None] = []
    for obs in observations:
        raw = obs.get("strike")
        if raw is not None:
            last = float(raw)
        strikes.append(last)
    return strikes


def _build_features_at(
    observations: list[dict],
    timestamps: list[int],
    strikes: list[float | None],
    idx: int,
    *,
    side: str,
) -> SnapshotFeatures | None:
    obs = observations[idx]
    sl = obs.get("seconds_left")
    if sl is None:
        return None

    strike = strikes[idx]
    btc = float(obs["btc_price"])
    ts = timestamps[idx]
    delta = (btc - strike) if strike is not None else None

    spread_now = _side_spread(obs, side)
    spread_lags: dict[int, float | None] = {}
    for w in SPREAD_LOOKBACK_WINDOWS:
        past_idx = find_idx_at_or_before(timestamps, idx, ts - w)
        spread_lags[w] = (
            _side_spread(observations[past_idx], side) if past_idx is not None else None
        )

    velocities: dict[int, float | None] = {}
    delta_changes: dict[int, float | None] = {}
    for w in BTC_VELOCITY_WINDOWS:
        past_idx = find_idx_at_or_before(timestamps, idx, ts - w)
        if past_idx is not None and strike is not None:
            past_btc = float(observations[past_idx]["btc_price"])
            velocities[w] = (btc - past_btc) / w
            delta_changes[w] = (btc - past_btc)
        else:
            velocities[w] = None
            delta_changes[w] = None

    spread_change = None
    if spread_now is not None and spread_lags.get(5) is not None:
        spread_change = spread_now - spread_lags[5]

    return SnapshotFeatures(
        timestamp=ts,
        seconds_left=int(sl),
        btc_price=btc,
        btc_delta=delta,
        spread_now=spread_now,
        spread_5s_ago=spread_lags.get(5),
        spread_10s_ago=spread_lags.get(10),
        spread_20s_ago=spread_lags.get(20),
        spread_change=spread_change,
        btc_velocity_5s=velocities.get(5),
        btc_velocity_10s=velocities.get(10),
        btc_velocity_20s=velocities.get(20),
        btc_velocity_30s=velocities.get(30),
        delta_5s=delta_changes.get(5),
        delta_10s=delta_changes.get(10),
        delta_20s=delta_changes.get(20),
        yes_ask=obs.get("yes_ask"),
        yes_bid=obs.get("yes_bid"),
        no_ask=obs.get("no_ask"),
        no_bid=obs.get("no_bid"),
    )


def _build_forward_outcomes(
    observations: list[dict],
    start_idx: int,
    direction: str,
    tp_levels: tuple[float, ...],
) -> ForwardOutcomes:
    bid_key = "yes_bid" if direction == "YES" else "no_bid"
    pending = set(tp_levels)
    outcomes: dict[float, tuple[float, int, bool]] = {}
    entry_bid = observations[start_idx].get(bid_key)
    exit_price = float(entry_bid) if entry_bid is not None else 0.0
    exit_ts = int(observations[start_idx]["timestamp"])

    for obs in observations[start_idx + 1:]:
        bid = obs.get(bid_key)
        if bid is None:
            continue
        bid_f = float(bid)
        exit_price = bid_f
        exit_ts = int(obs["timestamp"])
        for tp in list(pending):
            if bid_f >= tp:
                outcomes[tp] = (tp, exit_ts, True)
                pending.discard(tp)
        if not pending:
            break

    for tp in pending:
        outcomes[tp] = (exit_price, exit_ts, False)

    return ForwardOutcomes(by_tp=outcomes)
