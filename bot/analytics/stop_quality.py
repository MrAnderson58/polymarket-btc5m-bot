"""Section 32 — stop loss quality rating."""

from __future__ import annotations

from typing import Any

from bot.config import ER_V2_STOP_LOSS_PCT
from bot.report.analytics import _pnl_pct, trade_pnl


def _rating(excess_loss: float) -> str:
    if excess_loss <= 2:
        return "GOOD"
    if excess_loss <= 10:
        return "BAD"
    return "CRITICAL"


def _cause(excess_loss: float, holding_sec: float) -> str:
    if excess_loss > 15:
        return "Price gap"
    if excess_loss > 5 and holding_sec < 15:
        return "Fast gap / thin book"
    if excess_loss > 5:
        return "Slippage beyond stop"
    return "Within tolerance"


def build_stop_quality(closed: list[Any]) -> dict[str, Any]:
    stop_pct = abs(ER_V2_STOP_LOSS_PCT)
    stops = [t for t in closed if t["exit_reason"] == "STOP_LOSS"]
    items: list[dict[str, Any]] = []
    counts = {"GOOD": 0, "BAD": 0, "CRITICAL": 0}

    for trade in stops[-50:]:
        entry = float(trade["entry_price"])
        expected_pnl = _pnl_pct(entry, entry * (1 - stop_pct / 100))
        actual_pnl = trade_pnl(trade)
        excess = abs(actual_pnl - expected_pnl)
        holding = float(trade["holding_time_seconds"] or 0)
        rating = _rating(excess)
        counts[rating] += 1
        items.append(
            {
                "trade_id": int(trade["id"]),
                "entry": entry,
                "stop_pct": stop_pct,
                "expected_pnl_pct": round(expected_pnl, 2),
                "actual_pnl_pct": round(actual_pnl, 2),
                "excess_loss_pct": round(excess, 2),
                "holding_sec": round(holding, 1),
                "cause": _cause(excess, holding),
                "rating": rating,
            }
        )

    total = len(stops)
    return {
        "total_stops": total,
        "rating_counts": counts,
        "good_rate": counts["GOOD"] / total if total else 0.0,
        "stops": items,
    }
