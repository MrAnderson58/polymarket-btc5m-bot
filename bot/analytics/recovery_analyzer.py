"""Section 33 — extended post-stop recovery analysis."""

from __future__ import annotations

from typing import Any

from bot.analytics.intelligence_context import IntelligenceContext
from bot.er_btc_direction_stats import _exit_ts
from bot.report.analytics import SETTLEMENT_BID, _metrics, _pnl_pct, trade_pnl

RECOVERY_WINDOWS = (5, 10, 20, 30, 45, 60, 90, 120)
HOLD_SIM_SEC = (15, 30, 45, 90)


def _max_bid_after(
    series: list[tuple[int, float]],
    *,
    from_ts: int,
    window_sec: int,
) -> float | None:
    until = from_ts + window_sec
    bids = [b for ts, b in series if from_ts <= ts <= until]
    return max(bids) if bids else None


def build_recovery_analyzer(
    closed: list[Any],
    *,
    ctx: IntelligenceContext,
) -> dict[str, Any]:
    stops = [t for t in closed if t["exit_reason"] == "STOP_LOSS"]
    trade_rows: list[dict[str, Any]] = []
    hold_pnls: dict[int, list[float]] = {s: [] for s in HOLD_SIM_SEC}

    for trade in stops:
        entry = float(trade["entry_price"])
        exit_ts = _exit_ts(trade)
        end_ts = int(trade["end_ts"])
        series = ctx.cache.bid_series(
            market_slug=str(trade["market_slug"]),
            side=str(trade["side"]),
            start_ts=exit_ts,
            end_ts=end_ts,
        )
        if not series:
            continue

        peaks = {}
        for w in RECOVERY_WINDOWS:
            peaks[f"max_bid_{w}s"] = _max_bid_after(series, from_ts=exit_ts, window_sec=w)

        until_expiry = _max_bid_after(series, from_ts=exit_ts, window_sec=max(1, end_ts - exit_ts))
        peaks["max_bid_to_expiry"] = until_expiry

        for hold in HOLD_SIM_SEC:
            bid = _max_bid_after(series, from_ts=exit_ts, window_sec=hold)
            if bid is not None and bid + 1e-9 < SETTLEMENT_BID:
                hold_pnls[hold].append(_pnl_pct(entry, bid))

        trade_rows.append(
            {
                "trade_id": int(trade["id"]),
                "entry": entry,
                "actual_pnl_pct": round(trade_pnl(trade), 2),
                **{k: round(v, 4) if v is not None else None for k, v in peaks.items()},
            }
        )

    alt_holds = [
        {"hold_sec": sec, **_metrics(pnls)}
        for sec, pnls in hold_pnls.items()
        if pnls
    ]

    return {
        "analyzed_stops": len(trade_rows),
        "recovery_windows_sec": list(RECOVERY_WINDOWS),
        "alternative_holds": alt_holds,
        "trades": trade_rows[-30:],
    }
