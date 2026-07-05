"""Daily exit simulation."""

from __future__ import annotations

from bot.research.mtf.research_daily.config import (
    FIXED_SL_PCT, FIXED_TP_PCT, TIME_EXIT_SECONDS_LEFT,
    TRAIL_ACTIVATE_PCT, TRAIL_DISTANCE_PCT,
)
from bot.research.mtf.research_daily.models import ObsDaily, SimTradeDaily

EXIT_MODES = ("fixed_tp", "fixed_sl", "trailing", "time_exit", "settlement", "prob_reversal")


def _pnl(side, entry, exit_p):
    return (exit_p - entry) / entry * 100.0 if entry > 0 else 0.0


def _mark(obs: ObsDaily, side: str):
    if side == "YES":
        return obs.yes_bid if obs.yes_bid is not None else obs.yes_mid
    return obs.no_bid if obs.no_bid is not None else ((1 - obs.yes_mid) if obs.yes_mid else None)


def _entry(obs: ObsDaily, side: str):
    return obs.yes_ask if side == "YES" else obs.no_ask


def settlement(obs: ObsDaily, side: str) -> float:
    if obs.strike is None or obs.btc_price is None:
        mid = obs.yes_mid or 0.5
        return mid if side == "YES" else 1.0 - mid
    up = obs.btc_price >= obs.strike
    return 1.0 if (side == "YES" and up) or (side == "NO" and not up) else 0.0


def try_entry(path, idx, side):
    obs = path[idx]
    ep = _entry(obs, side)
    if ep is None or ep <= 0:
        return None
    return SimTradeDaily("", side, obs.market_slug, obs.timestamp, ep, obs.entry_second, session=obs.session)


def simulate_exit(trade: SimTradeDaily, path: list[ObsDaily], start_idx: int, mode: str) -> SimTradeDaily:
    entry, side = trade.entry_price, trade.side
    peak = trough = entry
    trail_on = False
    for j in range(start_idx + 1, len(path)):
        obs = path[j]
        mark = _mark(obs, side)
        if mark is None:
            continue
        pnl = _pnl(side, entry, mark)
        trade.mfe_pct = max(trade.mfe_pct, pnl)
        trade.mae_pct = min(trade.mae_pct, pnl)
        peak, trough = max(peak, mark), min(trough, mark)
        if mode == "fixed_tp" and pnl >= FIXED_TP_PCT:
            trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "fixed_tp"
            break
        if mode == "fixed_sl" and pnl <= -FIXED_SL_PCT:
            trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "fixed_sl"
            break
        if mode == "trailing" and pnl >= TRAIL_ACTIVATE_PCT:
            trail_on = True
        if mode == "trailing" and trail_on:
            stop = peak * (1 - TRAIL_DISTANCE_PCT / 100) if side == "YES" else trough * (1 + TRAIL_DISTANCE_PCT / 100)
            if (side == "YES" and mark <= stop) or (side == "NO" and mark >= stop):
                trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "trailing"
                break
        if mode == "time_exit" and obs.seconds_left is not None and obs.seconds_left <= TIME_EXIT_SECONDS_LEFT:
            trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "time_exit"
            break
        if mode == "prob_reversal" and obs.yes_mid is not None:
            if (side == "YES" and obs.yes_mid < 0.46) or (side == "NO" and obs.yes_mid > 0.54):
                trade.exit_ts, trade.exit_price, trade.exit_reason = obs.timestamp, mark, "prob_reversal"
                break
    if trade.exit_price is None:
        last = path[-1]
        trade.exit_ts = last.timestamp
        trade.exit_price = settlement(last, side) if mode == "settlement" else (_mark(last, side) or settlement(last, side))
        trade.exit_reason = "settlement" if mode == "settlement" else "window_end"
    trade.pnl_pct = _pnl(side, entry, trade.exit_price)
    return trade
