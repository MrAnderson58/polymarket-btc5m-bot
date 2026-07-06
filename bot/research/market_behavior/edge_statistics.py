"""Multidimensional edge observation collection — no feature look-ahead."""

from __future__ import annotations

from dataclasses import dataclass, field

from bot.research.market_behavior.analyzer import _resolve_strike
from bot.research.market_behavior.buckets import (
    btc_delta_bucket,
    entry_price_bucket,
    seconds_left_bucket,
    side_spread_cents,
    spread_bucket,
)
from bot.research.market_behavior.config import EDGE_TP_LEVELS, MIN_SECONDS_LEFT_FOR_EDGE
from bot.research.market_behavior.tp_probability import compute_cumulative_tp_hits


@dataclass
class EdgeObservation:
    direction: str
    entry_bucket: str
    entry_price: float
    btc_delta_bucket: str
    btc_delta_usd: float
    seconds_left_bucket: str
    seconds_left: int
    spread_bucket: str
    spread_cents: float
    tp_reached: dict[float, bool | None] = field(default_factory=dict)
    final_delta: float | None = None
    move_to_close: float | None = None
    max_excursion: float | None = None
    adverse_excursion: float | None = None
    final_bid: float | None = None


def _forward_bids(observations: list[dict], start_idx: int, side: str) -> list[float]:
    key = "yes_bid" if side == "YES" else "no_bid"
    out: list[float] = []
    for obs in observations[start_idx:]:
        val = obs.get(key)
        if val is not None:
            out.append(float(val))
    return out


def collect_edge_observations(observations: list[dict]) -> list[EdgeObservation]:
    """Build per-entry edge rows using entry-time features + forward path outcomes only."""
    if len(observations) < 2:
        return []

    strike = _resolve_strike(observations)
    if strike is None:
        return []

    final_btc = float(observations[-1]["btc_price"])
    final_delta = final_btc - strike
    samples: list[EdgeObservation] = []

    for idx, obs in enumerate(observations):
        sl_raw = obs.get("seconds_left")
        if sl_raw is None:
            continue
        sl = int(sl_raw)
        if sl < MIN_SECONDS_LEFT_FOR_EDGE:
            continue

        btc = float(obs["btc_price"])
        delta_usd = btc - strike
        delta_b = btc_delta_bucket(delta_usd)
        sl_b = seconds_left_bucket(sl)

        for side in ("YES", "NO"):
            ask_key = "yes_ask" if side == "YES" else "no_ask"
            entry_ask = obs.get(ask_key)
            if entry_ask is None:
                continue
            entry = float(entry_ask)
            if entry <= 0.05 or entry >= 0.95:
                continue

            spread_c = side_spread_cents(obs, side)
            if spread_c is None:
                continue

            entry_b, _ = entry_price_bucket(entry)
            sp_b = spread_bucket(spread_c)

            bids = _forward_bids(observations, idx, side)
            if not bids:
                continue

            final_bid = bids[-1]
            max_bid = max(bids)
            min_bid = min(bids)

            tp_hit = compute_cumulative_tp_hits(entry, max_bid)

            samples.append(EdgeObservation(
                direction=side,
                entry_bucket=entry_b,
                entry_price=entry,
                btc_delta_bucket=delta_b,
                btc_delta_usd=delta_usd,
                seconds_left_bucket=sl_b,
                seconds_left=sl,
                spread_bucket=sp_b,
                spread_cents=spread_c,
                tp_reached=tp_hit,
                final_delta=final_delta,
                move_to_close=final_btc - btc,
                max_excursion=max_bid - entry,
                adverse_excursion=entry - min_bid,
                final_bid=final_bid,
            ))

    return samples


def compute_ev(entry: float, tp: float, prob: float, avg_loss: float) -> tuple[float, float, float]:
    """Return (expected_profit, expected_loss, expected_value) for a TP setup."""
    win_profit = tp - entry
    expected_profit = prob * win_profit
    expected_loss = (1.0 - prob) * avg_loss
    ev = expected_profit - expected_loss
    return expected_profit, expected_loss, ev


def pnl_for_tp(obs: EdgeObservation, tp: float) -> float:
    """Realized PnL per share: TP bid target if hit, else exit at final bid."""
    hit = obs.tp_reached.get(tp)
    if hit is True:
        return tp - obs.entry_price
    if obs.final_bid is None:
        return -obs.entry_price
    return obs.final_bid - obs.entry_price
