"""Phase E.1 reversal confirmation — fixed parallel variants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.config import REVERSAL_CONFIGS
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP
from bot.research.market_events.price_feed import SymbolPriceState
from bot.research.market_events.shock_detector import ShockCandidate


@dataclass
class ReversalResult:
    variant: str
    confirmed: bool
    confirm_ts: int | None
    confirm_price: float | None
    notes: str = ""


def _reversal_direction(shock_direction: str) -> str:
    return SHOCK_DIRECTION_DOWN if shock_direction == SHOCK_DIRECTION_UP else SHOCK_DIRECTION_UP


def _price_move_pct(from_px: float, to_px: float, direction: str) -> float:
    if from_px <= 0:
        return 0.0
    if direction == SHOCK_DIRECTION_UP:
        return (to_px / from_px - 1.0) * 100.0
    return (from_px / to_px - 1.0) * 100.0


def confirm_r1(
    shock: ShockCandidate,
    state: SymbolPriceState,
    now_ts: int,
) -> ReversalResult:
    cfg = REVERSAL_CONFIGS["R1"]
    reclaim = float(cfg["reclaim_pct_of_shock"])
    shock_move = abs(shock.return_pct)
    if shock_move < 0.01 or state.last_price is None or not state.ticks:
        return ReversalResult("R1", False, None, None, "no_shock_move")
    prices = [t.price for t in state.ticks]
    if shock.direction == SHOCK_DIRECTION_DOWN:
        low = min(prices)
        high = max(prices[: max(1, len(prices) // 2)])
        shock_depth = max(high - low, 1e-9)
        move = (state.last_price - low) / shock_depth * shock_move
    else:
        high = max(prices)
        low = min(prices[: max(1, len(prices) // 2)])
        shock_depth = max(high - low, 1e-9)
        move = (high - state.last_price) / shock_depth * shock_move
    ok = move >= shock_move * reclaim
    return ReversalResult(
        "R1", ok, now_ts if ok else None, state.last_price if ok else None,
        f"reclaim={move:.3f}% need={shock_move * reclaim:.3f}%",
    )


def confirm_r2(
    shock: ShockCandidate,
    state: SymbolPriceState,
    now_ts: int,
) -> ReversalResult:
    bars = int(REVERSAL_CONFIGS["R2"]["local_reclaim_bars"])
    if len(state.ticks) < bars + 1 or state.last_price is None:
        return ReversalResult("R2", False, None, None, "insufficient_ticks")
    recent = list(state.ticks)[-bars:]
    if shock.direction == SHOCK_DIRECTION_DOWN:
        low = min(t.price for t in recent)
        ok = state.last_price > low * 1.001
    else:
        high = max(t.price for t in recent)
        ok = state.last_price < high * 0.999
    return ReversalResult(
        "R2", ok, now_ts if ok else None, state.last_price if ok else None,
        "local_reclaim",
    )


def confirm_r3(
    shock: ShockCandidate,
    state: SymbolPriceState,
    now_ts: int,
) -> ReversalResult:
    cfg = REVERSAL_CONFIGS["R3"]
    if state.last_price is None or len(state.ticks) < 3:
        return ReversalResult("R3", False, None, None, "insufficient_ticks")
    ret30 = state.return_over(30, now_ts) or 0.0
    ret10 = state.return_over(10, now_ts) or 0.0
    decay = abs(ret10) < abs(ret30) * float(cfg["velocity_decay_ratio"])
    rev_dir = _reversal_direction(shock.direction)
    event_px = state.ticks[0].price
    rev_move = _price_move_pct(event_px, state.last_price, rev_dir)
    ok = decay and rev_move >= float(cfg["min_reversal_return_pct"])
    return ReversalResult(
        "R3", ok, now_ts if ok else None, state.last_price if ok else None,
        f"decay={decay} rev={rev_move:.3f}%",
    )


def confirm_r4(
    shock: ShockCandidate,
    state: SymbolPriceState,
    now_ts: int,
) -> ReversalResult:
    cfg = REVERSAL_CONFIGS["R4"]
    vol_z = shock.volume_zscore or state.volume_zscore(60, now_ts)
    r1 = confirm_r1(shock, state, now_ts)
    ok = (
        vol_z is not None
        and vol_z >= float(cfg["volume_climax_zscore"])
        and r1.confirmed
    )
    return ReversalResult(
        "R4", ok, now_ts if ok else None, state.last_price if ok else None,
        f"vol_z={vol_z}",
    )


def confirm_r5(
    shock: ShockCandidate,
    state: SymbolPriceState,
    now_ts: int,
    *,
    first_confirm_ts: int | None = None,
) -> ReversalResult:
    survival = int(REVERSAL_CONFIGS["R5"]["survival_sec"])
    r1 = confirm_r1(shock, state, now_ts)
    if not r1.confirmed:
        return ReversalResult("R5", False, None, None, "r1_not_met")
    if first_confirm_ts is None:
        return ReversalResult("R5", False, None, None, "awaiting_survival")
    ok = (now_ts - first_confirm_ts) >= survival
    return ReversalResult(
        "R5", ok, now_ts if ok else None, state.last_price if ok else None,
        f"survival={(now_ts - first_confirm_ts)}s",
    )


def evaluate_all_reversals(
    shock: ShockCandidate,
    state: SymbolPriceState,
    now_ts: int,
    *,
    r5_first_confirm_ts: int | None = None,
) -> list[ReversalResult]:
    return [
        confirm_r1(shock, state, now_ts),
        confirm_r2(shock, state, now_ts),
        confirm_r3(shock, state, now_ts),
        confirm_r4(shock, state, now_ts),
        confirm_r5(shock, state, now_ts, first_confirm_ts=r5_first_confirm_ts),
    ]
