"""Causal entry-time features — observations at or before signal_ts only."""

from __future__ import annotations

from dataclasses import dataclass
import math

from bot.research.strategy_simulator.config import BTC_VELOCITY_WINDOWS, SPREAD_LOOKBACK_WINDOWS
from bot.research.strategy_simulator.features import SnapshotFeatures, _side_spread
from bot.research.strategy_simulator.path_index import find_idx_at_or_before


@dataclass
class CausalSnapshotFeatures(SnapshotFeatures):
    """Extended causal features for Strategy Discovery v2."""

    token_mid: float | None = None
    token_mid_change_5s: float | None = None
    token_mid_change_10s: float | None = None
    quote_momentum_5s: float | None = None
    quote_momentum_10s: float | None = None
    delta_velocity_5s: float | None = None
    delta_velocity_10s: float | None = None
    delta_acceleration_10s: float | None = None
    btc_volatility_30s: float | None = None
    normalized_distance: float | None = None
    complement_gap: float | None = None
    seconds_left_bucket: str = ""
    prior_aligned_snapshots: int = 0


def _token_mid(obs: dict, side: str) -> float | None:
    if side == "YES":
        bid, ask = obs.get("yes_bid"), obs.get("yes_ask")
    else:
        bid, ask = obs.get("no_bid"), obs.get("no_ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def _seconds_left_bucket(seconds_left: int) -> str:
    if seconds_left <= 30:
        return "0-30"
    if seconds_left <= 60:
        return "31-60"
    if seconds_left <= 90:
        return "61-90"
    if seconds_left <= 120:
        return "91-120"
    if seconds_left <= 180:
        return "121-180"
    return "181+"


def _btc_returns_volatility(
    observations: list[dict],
    timestamps: list[int],
    idx: int,
    *,
    window_sec: int = 30,
) -> float | None:
    ts = timestamps[idx]
    past_idx = find_idx_at_or_before(timestamps, idx, ts - window_sec)
    if past_idx is None or past_idx >= idx:
        return None
    returns: list[float] = []
    prev_btc = float(observations[past_idx]["btc_price"])
    for j in range(past_idx + 1, idx + 1):
        btc = float(observations[j]["btc_price"])
        if prev_btc > 0:
            returns.append((btc - prev_btc) / prev_btc)
        prev_btc = btc
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    return math.sqrt(var) if var > 0 else None


def build_causal_features(
    observations: list[dict],
    idx: int,
    *,
    side: str,
    timestamps: list[int] | None = None,
    strikes: list[float | None] | None = None,
) -> CausalSnapshotFeatures | None:
    """Build causal features using only observations[:idx+1]."""
    ts_list = timestamps or [int(o["timestamp"]) for o in observations]
    strike_list = strikes
    if strike_list is None:
        last: float | None = None
        strike_list = []
        for obs in observations[: idx + 1]:
            raw = obs.get("strike")
            if raw is not None:
                last = float(raw)
            strike_list.append(last)

    obs = observations[idx]
    sl = obs.get("seconds_left")
    if sl is None:
        return None

    strike = strike_list[idx]
    btc = float(obs["btc_price"])
    ts = ts_list[idx]
    delta = (btc - strike) if strike is not None else None

    spread_now = _side_spread(obs, side)
    spread_lags: dict[int, float | None] = {}
    for w in SPREAD_LOOKBACK_WINDOWS:
        past_idx = find_idx_at_or_before(ts_list, idx, ts - w)
        spread_lags[w] = (
            _side_spread(observations[past_idx], side) if past_idx is not None else None
        )

    velocities: dict[int, float | None] = {}
    delta_changes: dict[int, float | None] = {}
    for w in BTC_VELOCITY_WINDOWS:
        past_idx = find_idx_at_or_before(ts_list, idx, ts - w)
        if past_idx is not None and strike is not None:
            past_btc = float(observations[past_idx]["btc_price"])
            velocities[w] = (btc - past_btc) / w
            delta_changes[w] = btc - past_btc
        else:
            velocities[w] = None
            delta_changes[w] = None

    spread_change = None
    if spread_now is not None and spread_lags.get(5) is not None:
        spread_change = spread_now - spread_lags[5]

    mid_now = _token_mid(obs, side)
    mid_changes: dict[int, float | None] = {}
    quote_momentum: dict[int, float | None] = {}
    for w in (5, 10):
        past_idx = find_idx_at_or_before(ts_list, idx, ts - w)
        if past_idx is not None:
            past_mid = _token_mid(observations[past_idx], side)
            if mid_now is not None and past_mid is not None:
                mid_changes[w] = mid_now - past_mid
                quote_momentum[w] = mid_now - past_mid
            else:
                mid_changes[w] = None
                quote_momentum[w] = None
        else:
            mid_changes[w] = None
            quote_momentum[w] = None

    vol = _btc_returns_volatility(observations, ts_list, idx)
    norm_dist = None
    if delta is not None and vol is not None and vol > 1e-9:
        norm_dist = abs(delta) / (vol * btc)

    ya, na = obs.get("yes_ask"), obs.get("no_ask")
    complement_gap = None
    if ya is not None and na is not None:
        complement_gap = abs((float(ya) + float(na)) - 1.0)

    delta_accel = None
    if velocities.get(10) is not None and velocities.get(5) is not None:
        delta_accel = velocities[5] - velocities[10]

    prior_aligned = 0
    if delta is not None:
        for lookback in (5, 10):
            past_idx = find_idx_at_or_before(ts_list, idx, ts - lookback)
            if past_idx is None or strike_list[past_idx] is None:
                continue
            past_delta = btc - float(observations[past_idx]["btc_price"])
            if side == "YES" and past_delta >= 0:
                prior_aligned += 1
            elif side == "NO" and past_delta <= 0:
                prior_aligned += 1

    return CausalSnapshotFeatures(
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
        token_mid=mid_now,
        token_mid_change_5s=mid_changes.get(5),
        token_mid_change_10s=mid_changes.get(10),
        quote_momentum_5s=quote_momentum.get(5),
        quote_momentum_10s=quote_momentum.get(10),
        delta_velocity_5s=velocities.get(5),
        delta_velocity_10s=velocities.get(10),
        delta_acceleration_10s=delta_accel,
        btc_volatility_30s=vol,
        normalized_distance=norm_dist,
        complement_gap=complement_gap,
        seconds_left_bucket=_seconds_left_bucket(int(sl)),
        prior_aligned_snapshots=prior_aligned,
    )
