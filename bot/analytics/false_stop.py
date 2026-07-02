"""Section 34 — false / true / late stop classification."""

from __future__ import annotations

import statistics
from typing import Any

from bot.analytics.intelligence_context import IntelligenceContext
from bot.er_btc_direction_stats import _exit_ts
from bot.report.analytics import SETTLEMENT_BID


def build_false_stop_detector(
    closed: list[Any],
    *,
    ctx: IntelligenceContext,
) -> dict[str, Any]:
    stops = [t for t in closed if t["exit_reason"] == "STOP_LOSS"]
    false_stops: list[dict[str, Any]] = []
    true_stops = 0
    late_stops = 0
    false_count = 0

    hold_times = [float(t["holding_time_seconds"] or 0) for t in stops]
    median_hold = statistics.median(hold_times) if hold_times else 30.0

    for trade in stops:
        entry = float(trade["entry_price"])
        exit_ts = _exit_ts(trade)
        holding = float(trade["holding_time_seconds"] or 0)
        series = ctx.cache.bid_series(
            market_slug=str(trade["market_slug"]),
            side=str(trade["side"]),
            start_ts=exit_ts,
            end_ts=exit_ts + 120,
        )
        recovered_sec = None
        for ts, bid in series:
            if bid + 1e-9 >= SETTLEMENT_BID:
                continue
            if bid >= entry and recovered_sec is None:
                recovered_sec = ts - exit_ts
                break

        if recovered_sec is not None and recovered_sec <= 15:
            false_count += 1
            false_stops.append(
                {
                    "trade_id": int(trade["id"]),
                    "entry": entry,
                    "recovered_above_entry_sec": recovered_sec,
                    "classification": "False Stop",
                }
            )
        elif holding > median_hold * 1.8:
            late_stops += 1
        else:
            true_stops += 1

    total = len(stops)
    return {
        "total_stops": total,
        "false_stops": false_count,
        "true_stops": true_stops,
        "late_stops": late_stops,
        "false_stop_rate": false_count / total if total else 0.0,
        "examples": false_stops[:10],
    }
