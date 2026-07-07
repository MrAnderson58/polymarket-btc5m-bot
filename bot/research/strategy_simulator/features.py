"""Entry-time features: BTC velocity, delta changes, spread dynamics."""

from __future__ import annotations

from dataclasses import dataclass

from bot.research.market_behavior.analyzer import _resolve_strike
from bot.research.strategy_simulator.config import BTC_VELOCITY_WINDOWS, SPREAD_LOOKBACK_WINDOWS
from bot.research.strategy_simulator.path_index import find_idx_at_or_before


@dataclass
class SnapshotFeatures:
    timestamp: int
    seconds_left: int
    btc_price: float
    btc_delta: float | None
    spread_now: float | None
    spread_5s_ago: float | None = None
    spread_10s_ago: float | None = None
    spread_20s_ago: float | None = None
    spread_change: float | None = None
    btc_velocity_5s: float | None = None
    btc_velocity_10s: float | None = None
    btc_velocity_20s: float | None = None
    btc_velocity_30s: float | None = None
    delta_5s: float | None = None
    delta_10s: float | None = None
    delta_20s: float | None = None
    yes_ask: float | None = None
    yes_bid: float | None = None
    no_ask: float | None = None
    no_bid: float | None = None


def _side_spread(obs: dict, side: str) -> float | None:
    if side == "YES":
        bid, ask = obs.get("yes_bid"), obs.get("yes_ask")
    else:
        bid, ask = obs.get("no_bid"), obs.get("no_ask")
    if bid is None or ask is None:
        return None
    return max(0.0, float(ask) - float(bid))


def _find_obs_at_or_before(observations: list[dict], idx: int, target_ts: int) -> dict | None:
    timestamps = [int(o["timestamp"]) for o in observations]
    past_idx = find_idx_at_or_before(timestamps, idx, target_ts)
    return observations[past_idx] if past_idx is not None else None


def build_snapshot_features(
    observations: list[dict],
    idx: int,
    *,
    side: str,
) -> SnapshotFeatures | None:
    """Features at observation idx using only data at or before idx."""
    obs = observations[idx]
    sl = obs.get("seconds_left")
    if sl is None:
        return None

    timestamps = [int(o["timestamp"]) for o in observations]
    strike = _resolve_strike(observations[: idx + 1])
    btc = float(obs["btc_price"])
    ts = int(obs["timestamp"])
    delta = (btc - strike) if strike is not None else None

    spread_now = _side_spread(obs, side)
    spread_lags: dict[int, float | None] = {}
    for w in SPREAD_LOOKBACK_WINDOWS:
        past = _find_obs_at_or_before(observations, idx, ts - w)
        spread_lags[w] = _side_spread(past, side) if past else None

    velocities: dict[int, float | None] = {}
    delta_changes: dict[int, float | None] = {}
    for w in BTC_VELOCITY_WINDOWS:
        past = _find_obs_at_or_before(observations, idx, ts - w)
        if past and strike is not None:
            past_btc = float(past["btc_price"])
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
