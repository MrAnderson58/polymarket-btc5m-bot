"""Offline trade replay for parameter grid search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExitKind = Literal["STOP", "TRAILING", "TIME", "HOLD"]


@dataclass(frozen=True)
class StrategyParams:
    max_entry: float
    stop_pct: float
    trailing_activation: float
    trailing_distance: float
    time_stop_sec: int
    btc_filter_usd: float


@dataclass
class TradeReplay:
    trade_id: int
    entry_price: float
    entry_ts: int
    actual_pnl: float
    btc_move_30s: float | None
    bids: list[tuple[int, float]]  # (offset_sec from entry, bid)


def _pnl(entry: float, exit_bid: float) -> float:
    return (exit_bid - entry) / entry * 100


def simulate_trade(
    trade: TradeReplay,
    params: StrategyParams,
) -> tuple[float | None, ExitKind | None]:
    """Return (pnl%, exit_kind) or (None, None) if filtered out."""
    if trade.entry_price > params.max_entry + 1e-9:
        return None, None
    if trade.btc_move_30s is not None and trade.btc_move_30s > params.btc_filter_usd:
        return None, None

    entry = trade.entry_price
    stop_price = entry * (1 + params.stop_pct / 100)
    active = False
    high_water = entry
    last_bid = entry

    for offset, bid in trade.bids:
        last_bid = bid
        if offset > params.time_stop_sec:
            return _pnl(entry, last_bid), "TIME"
        if bid <= stop_price + 1e-9:
            return _pnl(entry, stop_price), "STOP"
        if not active and bid - entry + 1e-9 >= params.trailing_activation:
            active = True
            high_water = bid
        if active:
            high_water = max(high_water, bid)
            if bid <= high_water - params.trailing_distance + 1e-9:
                return _pnl(entry, bid), "TRAILING"

    return _pnl(entry, last_bid), "HOLD"


def simulate_batch(
    trades: list[TradeReplay],
    params: StrategyParams,
) -> dict[str, float | int]:
    pnls: list[float] = []
    wins = 0
    stops = 0
    for trade in trades:
        pnl, kind = simulate_trade(trade, params)
        if pnl is None:
            continue
        pnls.append(pnl)
        if pnl > 0:
            wins += 1
        if kind == "STOP":
            stops += 1
    if not pnls:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "net_profit": 0.0,
            "profit_factor": 0.0,
            "stop_rate": 0.0,
        }
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    return {
        "trades": len(pnls),
        "win_rate": wins / len(pnls),
        "avg_pnl": sum(pnls) / len(pnls),
        "net_profit": gross_win - gross_loss,
        "profit_factor": gross_win / gross_loss if gross_loss else float("inf"),
        "stop_rate": stops / len(pnls),
    }
