"""Multidimensional bucket assignment for edge statistics."""

from __future__ import annotations

from bot.research.market_behavior.config import (
    BTC_DELTA_BUCKET_EDGES,
    ENTRY_BUCKET_WIDTH,
    SECONDS_LEFT_THRESHOLDS,
    SPREAD_BUCKET_EDGES_CENTS,
)


def entry_price_bucket(price: float, *, width: float = ENTRY_BUCKET_WIDTH) -> tuple[str, float]:
    step = int(width * 100)
    lo = int(price * 100 // step) * step
    hi = lo + step
    mid = (lo + hi) / 2 / 100.0
    return f"{lo:02d}-{hi:02d}c", mid


def btc_delta_bucket(delta_usd: float) -> str:
    """Bucket BTC price minus strike (USD) using configured edges."""
    edges = BTC_DELTA_BUCKET_EDGES
    if delta_usd < edges[0]:
        return f"<{edges[0]:.0f}"
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo <= delta_usd < hi:
            return f"{lo:.0f}...{hi:.0f}"
    return f">{edges[-1]:.0f}"


def seconds_left_bucket(seconds_left: int) -> str:
    """Upper-bound bucket: 25s -> <=30, 250s -> >=240."""
    if seconds_left > SECONDS_LEFT_THRESHOLDS[-1]:
        return f">={SECONDS_LEFT_THRESHOLDS[-1]}"
    for threshold in SECONDS_LEFT_THRESHOLDS:
        if seconds_left <= threshold:
            return f"<={threshold}"
    return f"<={SECONDS_LEFT_THRESHOLDS[0]}"


def spread_bucket(spread_cents: float) -> str:
    """Bucket quoted spread in cents (ask - bid)."""
    edges = SPREAD_BUCKET_EDGES_CENTS
    if spread_cents <= edges[0]:
        return f"0-{edges[0]:.0f}c"
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo < spread_cents <= hi:
            return f"{lo:.0f}-{hi:.0f}c"
    return f">{edges[-1]:.0f}c"


def side_spread_cents(obs: dict, side: str) -> float | None:
    if side == "YES":
        bid, ask = obs.get("yes_bid"), obs.get("yes_ask")
    else:
        bid, ask = obs.get("no_bid"), obs.get("no_ask")
    if bid is None or ask is None:
        return None
    return max(0.0, (float(ask) - float(bid)) * 100.0)
