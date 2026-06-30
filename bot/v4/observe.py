"""Observation helpers for V4 Shadow."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ObservationSnapshot:
    timestamp: int
    window_start: int
    seconds_from_start: int
    seconds_left: int
    btc_price: float
    strike: float | None
    delta: float | None
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    trend_score: float | None
    trend_side: str | None
    spread: float | None


def compute_spread(yes_bid: float | None, yes_ask: float | None) -> float | None:
    if yes_bid is None or yes_ask is None:
        return None
    return yes_ask - yes_bid


def build_observation(
    *,
    now_ts: int,
    window_start: int,
    end_ts: int,
    btc_price: float,
    strike: float | None,
    quotes: dict[str, float | None],
    trend_score: float | None = None,
    trend_side: str | None = None,
) -> ObservationSnapshot:
    seconds_from_start = now_ts - window_start
    seconds_left = max(0, end_ts - now_ts)
    delta = btc_price - strike if strike is not None else None
    yes_bid = quotes.get("yes_bid")
    yes_ask = quotes.get("yes_ask")
    return ObservationSnapshot(
        timestamp=now_ts,
        window_start=window_start,
        seconds_from_start=seconds_from_start,
        seconds_left=seconds_left,
        btc_price=btc_price,
        strike=strike,
        delta=delta,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=quotes.get("no_bid"),
        no_ask=quotes.get("no_ask"),
        trend_score=trend_score,
        trend_side=trend_side,
        spread=compute_spread(yes_bid, yes_ask),
    )


def in_observe_phase(seconds_from_start: int, observe_seconds: int) -> bool:
    return seconds_from_start < observe_seconds


def log_observe(snapshot: ObservationSnapshot) -> None:
    logger.info(
        "V4 OBSERVE | t=%ss left=%ss btc=%.2f delta=%s side=%s score=%s spread=%s",
        snapshot.seconds_from_start,
        snapshot.seconds_left,
        snapshot.btc_price,
        f"{snapshot.delta:.2f}" if snapshot.delta is not None else "-",
        snapshot.trend_side or "-",
        f"{snapshot.trend_score:.1f}" if snapshot.trend_score is not None else "-",
        f"{snapshot.spread:.3f}" if snapshot.spread is not None else "-",
    )
