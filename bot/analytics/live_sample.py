"""Live sample size since last configuration change."""

from __future__ import annotations

from typing import Any

MIN_LIVE_TRADES = 300
TARGET_LIVE_TRADES = 500


def build_live_sample(
    experiments: list[dict[str, Any]],
    total_trades: int,
) -> dict[str, Any]:
    if not experiments:
        since = total_trades
    else:
        active = experiments[-1]
        at_start = int(active.get("trades_at_start", 0))
        since = max(0, total_trades - at_start)

    sufficient = since >= MIN_LIVE_TRADES
    return {
        "sufficient": sufficient,
        "target_met": since >= TARGET_LIVE_TRADES,
        "current_since_change": since,
        "total_trades": total_trades,
        "required_min": MIN_LIVE_TRADES,
        "required_target": TARGET_LIVE_TRADES,
        "trades_needed": max(0, MIN_LIVE_TRADES - since),
        "trades_until_review": max(0, TARGET_LIVE_TRADES - since),
        "message": (
            f"INSUFFICIENT LIVE DATA — {since} trades since last change, "
            f"need {MIN_LIVE_TRADES}–{TARGET_LIVE_TRADES}"
            if not sufficient
            else f"Sufficient live sample: {since} trades since last change"
        ),
    }
