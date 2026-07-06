"""Signal vs market price sanity — research labels only, never auto-reject."""

from __future__ import annotations

from typing import Any

from bot.research.futures_agent.config import (
    SIGNAL_MARKET_ENTRY_ALREADY_PASSED,
    SIGNAL_MARKET_ENTRY_NEAR,
    SIGNAL_MARKET_ENTRY_PENDING,
    SIGNAL_MARKET_INVALID_PRICE,
    SIGNAL_MARKET_STALE,
)

NEAR_MARKET_PCT = 2.0
STALE_DISTANCE_PCT = 20.0


def _pct_distance(from_price: float, to_price: float) -> float:
    if to_price == 0:
        return 0.0
    return (from_price - to_price) / to_price * 100.0


def compute_signal_market_sanity(
    *,
    direction: str | None,
    entry_low: float | None,
    entry_high: float | None,
    stop_loss: float | None,
    take_profits: list[float],
    market_price: float | None,
) -> dict[str, Any]:
    """Direction-aware entry/stop/TP distance vs snapshot market price."""
    result: dict[str, Any] = {
        "entry_distance_pct": None,
        "stop_distance_pct": None,
        "tp_distance_pct": None,
        "signal_market_status": SIGNAL_MARKET_INVALID_PRICE,
    }

    if market_price is None or market_price <= 0:
        return result

    entry_lo = entry_low
    entry_hi = entry_high if entry_high is not None else entry_low
    if entry_lo is None and entry_hi is None:
        return result

    if entry_lo is None:
        entry_lo = entry_hi
    if entry_hi is None:
        entry_hi = entry_lo

    entry_mid = (entry_lo + entry_hi) / 2.0
    result["entry_distance_pct"] = _pct_distance(market_price, entry_mid)

    side = (direction or "").upper()
    dist = abs(result["entry_distance_pct"])

    if dist <= NEAR_MARKET_PCT:
        status = SIGNAL_MARKET_ENTRY_NEAR
    elif dist > STALE_DISTANCE_PCT:
        status = SIGNAL_MARKET_STALE
    elif side == "LONG":
        if market_price > entry_hi:
            status = SIGNAL_MARKET_ENTRY_PENDING
        elif market_price < entry_lo:
            status = SIGNAL_MARKET_ENTRY_ALREADY_PASSED
        else:
            status = SIGNAL_MARKET_ENTRY_NEAR
    elif side == "SHORT":
        if market_price < entry_lo:
            status = SIGNAL_MARKET_ENTRY_PENDING
        elif market_price > entry_hi:
            status = SIGNAL_MARKET_ENTRY_ALREADY_PASSED
        else:
            status = SIGNAL_MARKET_ENTRY_NEAR
    else:
        status = SIGNAL_MARKET_STALE if dist > STALE_DISTANCE_PCT else SIGNAL_MARKET_ENTRY_NEAR

    result["signal_market_status"] = status

    if stop_loss is not None:
        result["stop_distance_pct"] = _pct_distance(stop_loss, market_price)

    if take_profits:
        tp_dists = [_pct_distance(tp, market_price) for tp in take_profits]
        if side == "LONG":
            result["tp_distance_pct"] = min(tp_dists)
        elif side == "SHORT":
            result["tp_distance_pct"] = max(tp_dists)
        else:
            result["tp_distance_pct"] = min(abs(d) for d in tp_dists)

    return result
