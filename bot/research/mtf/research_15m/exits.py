"""Exit models for 15m research — evaluated separately from entry discovery."""

from __future__ import annotations

from bot.research.mtf.research_15m.config import (
    FIXED_SL_PCT,
    FIXED_TP_PCT,
    TIME_EXIT_SECONDS_LEFT,
    TRAIL_ACTIVATE_PCT,
    TRAIL_DISTANCE_PCT,
)
from bot.research.mtf.research_15m.models import Obs15m, SimTrade


def _pnl_pct(side: str, entry: float, exit_p: float) -> float:
    if entry <= 0:
        return 0.0
    return (exit_p - entry) / entry * 100.0


def _mark_price(obs: Obs15m, side: str) -> float | None:
    if side == "YES":
        return obs.yes_bid if obs.yes_bid is not None else obs.yes_mid
    return obs.no_bid if obs.no_bid is not None else (
        (1 - obs.yes_mid) if obs.yes_mid is not None else None
    )


def _entry_price(obs: Obs15m, side: str) -> float | None:
    return obs.yes_ask if side == "YES" else obs.no_ask


def settlement_price(obs: Obs15m, side: str) -> float:
    """Settlement proxy: BTC vs strike at window end."""
    if obs.strike is None or obs.btc_price is None:
        mid = obs.yes_mid or 0.5
        return mid if side == "YES" else 1.0 - mid
    up = obs.btc_price >= obs.strike
    return 1.0 if (side == "YES" and up) or (side == "NO" and not up) else 0.0


def simulate_exit(
    trade: SimTrade,
    path: list[Obs15m],
    start_idx: int,
    exit_mode: str,
) -> SimTrade:
    entry = trade.entry_price
    side = trade.side
    peak = trough = entry
    trail_active = False
    trail_stop = None

    for j in range(start_idx + 1, len(path)):
        obs = path[j]
        mark = _mark_price(obs, side)
        if mark is None:
            continue

        pnl_now = _pnl_pct(side, entry, mark)
        trade.mfe_pct = max(trade.mfe_pct, pnl_now)
        trade.mae_pct = min(trade.mae_pct, pnl_now)
        peak = max(peak, mark)
        trough = min(trough, mark)

        if exit_mode == "fixed_tp" and pnl_now >= FIXED_TP_PCT:
            trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "fixed_tp"
            break
        if exit_mode == "fixed_sl" and pnl_now <= -FIXED_SL_PCT:
            trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "fixed_sl"
            break
        if exit_mode == "trailing":
            if pnl_now >= TRAIL_ACTIVATE_PCT:
                trail_active = True
            if trail_active:
                trail_stop = peak * (1 - TRAIL_DISTANCE_PCT / 100.0) if side == "YES" else trough * (1 + TRAIL_DISTANCE_PCT / 100.0)
                if side == "YES" and mark <= (trail_stop or 0):
                    trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "trailing"
                    break
                if side == "NO" and mark >= (trail_stop or 1):
                    trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "trailing"
                    break
        if exit_mode == "time_exit" and obs.seconds_left is not None and obs.seconds_left <= TIME_EXIT_SECONDS_LEFT:
            trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "time_exit"
            break
        if exit_mode == "prob_reversal" and obs.yes_mid is not None:
            if side == "YES" and obs.yes_mid < 0.48:
                trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "prob_reversal"
                break
            if side == "NO" and obs.yes_mid > 0.52:
                trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "prob_reversal"
                break

    if trade.exit_price is None:
        last = path[-1]
        trade.exit_ts = last.timestamp
        trade.exit_price = settlement_price(last, side) if exit_mode == "settlement" else (_mark_price(last, side) or settlement_price(last, side))
        trade.exit_reason = "settlement" if exit_mode == "settlement" else "window_end"

    trade.pnl_pct = _pnl_pct(side, entry, trade.exit_price)
    return trade


EXIT_MODES = (
    "fixed_tp",
    "fixed_sl",
    "trailing",
    "time_exit",
    "settlement",
    "prob_reversal",
)


def try_entry(path: list[Obs15m], idx: int, side: str) -> SimTrade | None:
    obs = path[idx]
    ep = _entry_price(obs, side)
    if ep is None or ep <= 0:
        return None
    return SimTrade(
        family="",
        side=side,
        market_slug=obs.market_slug,
        entry_ts=obs.timestamp,
        entry_price=ep,
        entry_second=obs.entry_second,
    )
