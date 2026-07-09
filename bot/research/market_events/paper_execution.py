"""Phase E.1 paper exit policies — fixed parallel variants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import DEFAULT_FEE_BPS, DEFAULT_SLIPPAGE_BPS, EXIT_POLICIES
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP


@dataclass
class PaperPosition:
    event_id: int
    symbol: str
    direction: str
    reversal_variant: str
    exit_variant: str
    entry_ts: int
    entry_price: float
    initial_stop: float
    be_trigger_pct: float | None = None
    trail_trigger_pct: float | None = None
    trail_pct: float | None = None
    tp_pct: float | None = None
    partial_tp_pct: float | None = None
    partial_frac: float = 0.0
    stop_price: float = 0.0
    be_active: bool = False
    trail_active: bool = False
    trail_anchor: float | None = None
    remaining_frac: float = 1.0
    mfe: float = 0.0
    mae: float = 0.0
    closed: bool = False
    exit_ts: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_return: float | None = None
    be_exit: bool = False
    partial_taken: bool = False


@dataclass
class ExitTickResult:
    closed: bool
    exit_reason: str | None = None
    exit_price: float | None = None
    gross_return: float | None = None


def _signed_return(entry: float, px: float, direction: str) -> float:
    if entry <= 0:
        return 0.0
    if direction == SHOCK_DIRECTION_UP:
        return (px / entry - 1.0) * 100.0
    return (entry / px - 1.0) * 100.0


def _stop_for_entry(entry: float, direction: str, stop_pct: float) -> float:
    if direction == SHOCK_DIRECTION_UP:
        return entry * (1.0 - stop_pct / 100.0)
    return entry * (1.0 + stop_pct / 100.0)


def open_paper_position(
    *,
    event_id: int,
    symbol: str,
    direction: str,
    reversal_variant: str,
    exit_variant: str,
    entry_ts: int,
    entry_price: float,
) -> PaperPosition:
    cfg = EXIT_POLICIES[exit_variant]
    stop_pct = float(cfg["hard_stop_pct"])
    trade_dir = SHOCK_DIRECTION_UP if direction == SHOCK_DIRECTION_DOWN else SHOCK_DIRECTION_DOWN
    stop = _stop_for_entry(entry_price, trade_dir, stop_pct)
    pos = PaperPosition(
        event_id=event_id,
        symbol=symbol,
        direction=trade_dir,
        reversal_variant=reversal_variant,
        exit_variant=exit_variant,
        entry_ts=entry_ts,
        entry_price=entry_price,
        initial_stop=stop,
        stop_price=stop,
        be_trigger_pct=cfg.get("be_trigger_pct"),
        trail_trigger_pct=cfg.get("trail_trigger_pct"),
        trail_pct=cfg.get("trail_pct"),
        tp_pct=cfg.get("tp_pct"),
        partial_tp_pct=cfg.get("partial_tp_pct"),
        partial_frac=float(cfg.get("partial_frac", 0.0)),
    )
    return pos


def process_exit_tick(pos: PaperPosition, *, ts: int, price: float) -> ExitTickResult:
    if pos.closed:
        return ExitTickResult(closed=True, exit_reason=pos.exit_reason)

    ret = _signed_return(pos.entry_price, price, pos.direction)
    pos.mfe = max(pos.mfe, ret)
    pos.mae = min(pos.mae, ret)

    if pos.direction == SHOCK_DIRECTION_UP:
        if price <= pos.stop_price:
            return _close(pos, ts, price, "STOP" if not pos.be_active else "BE_STOP")
    else:
        if price >= pos.stop_price:
            return _close(pos, ts, price, "STOP" if not pos.be_active else "BE_STOP")

    if pos.be_trigger_pct is not None and not pos.be_active and ret >= pos.be_trigger_pct:
        pos.be_active = True
        pos.stop_price = pos.entry_price

    if pos.trail_trigger_pct is not None and ret >= pos.trail_trigger_pct:
        pos.trail_active = True
        pos.trail_anchor = price

    if pos.trail_active and pos.trail_pct and pos.trail_anchor:
        trail_stop = (
            pos.trail_anchor * (1.0 - pos.trail_pct / 100.0)
            if pos.direction == SHOCK_DIRECTION_UP
            else pos.trail_anchor * (1.0 + pos.trail_pct / 100.0)
        )
        if pos.direction == SHOCK_DIRECTION_UP:
            pos.stop_price = max(pos.stop_price, trail_stop)
        else:
            pos.stop_price = min(pos.stop_price, trail_stop)

    if pos.partial_tp_pct and not pos.partial_taken and ret >= pos.partial_tp_pct:
        pos.partial_taken = True
        pos.remaining_frac = max(0.0, 1.0 - pos.partial_frac)
        if pos.be_trigger_pct:
            pos.be_active = True
            pos.stop_price = pos.entry_price

    if pos.tp_pct and ret >= pos.tp_pct:
        return _close(pos, ts, price, "TP")

    return ExitTickResult(closed=False)


def _close(pos: PaperPosition, ts: int, price: float, reason: str) -> ExitTickResult:
    pos.closed = True
    pos.exit_ts = ts
    pos.exit_price = price
    pos.exit_reason = reason
    pos.be_exit = reason == "BE_STOP"
    gross = _signed_return(pos.entry_price, price, pos.direction) * pos.remaining_frac
    if pos.partial_taken:
        partial_ret = float(pos.partial_tp_pct or 0) * pos.partial_frac
        gross += partial_ret
    pos.gross_return = gross
    return ExitTickResult(closed=True, exit_reason=reason, exit_price=price, gross_return=gross)


def net_return(gross: float, *, fee_bps: float = DEFAULT_FEE_BPS, slippage_bps: float = DEFAULT_SLIPPAGE_BPS) -> float:
    cost = (fee_bps + slippage_bps) * 2 / 100.0
    return gross - cost
