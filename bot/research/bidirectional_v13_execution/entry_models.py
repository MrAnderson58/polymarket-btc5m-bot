"""Entry execution models — never assume fill without quote evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bot.research.bidirectional_live_audit import LiveTrade
from bot.research.bidirectional_v13_execution.quote_path import QuotePathContext, QuoteObservation


@dataclass(frozen=True)
class EntryFill:
    filled: bool
    entry_price: float | None
    fill_ts: int | None
    reason: str


def _side_ask(obs: QuoteObservation, side: str) -> float | None:
    return obs.yes_ask if side == "YES" else obs.no_ask


def _obs_at_or_before(ctx: QuotePathContext, ts: int) -> QuoteObservation | None:
    best: QuoteObservation | None = None
    for obs in ctx.observations:
        if obs.timestamp > ts:
            break
        best = obs
    return best


def _first_ask_at_or_after(ctx: QuotePathContext, target_ts: int, max_lag: int = 5) -> tuple[float | None, int | None]:
    for obs in ctx.observations:
        if obs.timestamp < target_ts:
            continue
        if obs.timestamp > target_ts + max_lag:
            break
        ask = _side_ask(obs, ctx.side)
        if ask is not None and ask > 0:
            return ask, obs.timestamp
    return None, None


def _passive_fill(ctx: QuotePathContext, limit: float, valid_sec: int) -> EntryFill:
    end_ts = ctx.signal_ts + valid_sec
    for obs in ctx.observations:
        if obs.timestamp <= ctx.signal_ts:
            continue
        if obs.timestamp > end_ts:
            break
        ask = _side_ask(obs, ctx.side)
        if ask is not None and ask <= limit + 1e-6:
            return EntryFill(True, ask, obs.timestamp, f"passive_limit_{valid_sec}s")
    return EntryFill(False, None, None, f"passive_no_fill_{valid_sec}s")


def _persistence_fill(ctx: QuotePathContext, n_consecutive: int) -> EntryFill:
    if ctx.signal_ask is None:
        return EntryFill(False, None, None, "no_signal_ask")
    streak = 0
    last_ask: float | None = None
    last_ts: int | None = None
    for obs in ctx.observations:
        if obs.timestamp < ctx.signal_ts:
            continue
        ask = _side_ask(obs, ctx.side)
        if ask is None:
            streak = 0
            continue
        if abs(ask - ctx.signal_ask) <= 0.005:
            streak += 1
            last_ask = ask
            last_ts = obs.timestamp
            if streak >= n_consecutive:
                return EntryFill(True, last_ask, last_ts, f"persistence_{n_consecutive}")
        else:
            streak = 0
    return EntryFill(False, None, None, f"persistence_fail_{n_consecutive}")


def model_immediate_taker(ctx: QuotePathContext, _trade: LiveTrade) -> EntryFill:
    if ctx.signal_ask is None:
        return EntryFill(False, None, None, "no_signal_ask")
    return EntryFill(True, ctx.signal_ask, ctx.signal_ts, "immediate_taker")


def model_delayed_taker(ctx: QuotePathContext, _trade: LiveTrade, delay: int) -> EntryFill:
    ask, ts = _first_ask_at_or_after(ctx, ctx.signal_ts + delay)
    if ask is None:
        return EntryFill(False, None, None, f"delayed_{delay}s_no_quote")
    return EntryFill(True, ask, ts, f"delayed_taker_{delay}s")


def model_passive_limit(ctx: QuotePathContext, _trade: LiveTrade, valid_sec: int) -> EntryFill:
    if ctx.signal_ask is None:
        return EntryFill(False, None, None, "no_signal_ask")
    return _passive_fill(ctx, ctx.signal_ask, valid_sec)


def model_skip_spread(ctx: QuotePathContext, _trade: LiveTrade, max_spread: float) -> EntryFill:
    if ctx.spread is None or ctx.signal_ask is None:
        return EntryFill(False, None, None, "no_spread_data")
    if ctx.spread > max_spread:
        return EntryFill(False, None, None, f"spread>{max_spread}")
    return EntryFill(True, ctx.signal_ask, ctx.signal_ts, f"spread_ok_{max_spread}")


def model_persistence(ctx: QuotePathContext, _trade: LiveTrade, n: int) -> EntryFill:
    return _persistence_fill(ctx, n)


ENTRY_MODELS: dict[str, Callable[[QuotePathContext, LiveTrade], EntryFill]] = {
    "A_immediate_taker": model_immediate_taker,
    "B_delayed_1s": lambda c, t: model_delayed_taker(c, t, 1),
    "C_delayed_2s": lambda c, t: model_delayed_taker(c, t, 2),
    "D_delayed_3s": lambda c, t: model_delayed_taker(c, t, 3),
    "E_passive_3s": lambda c, t: model_passive_limit(c, t, 3),
    "F_passive_5s": lambda c, t: model_passive_limit(c, t, 5),
    "G_skip_spread_002": lambda c, t: model_skip_spread(c, t, 0.02),
    "H_skip_spread_003": lambda c, t: model_skip_spread(c, t, 0.03),
    "I_persistence_2": lambda c, t: model_persistence(c, t, 2),
    "J_persistence_3": lambda c, t: model_persistence(c, t, 3),
}

PASSIVE_MODELS = frozenset({"E_passive_3s", "F_passive_5s"})
