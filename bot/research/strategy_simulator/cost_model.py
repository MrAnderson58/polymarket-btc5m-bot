"""Configurable execution cost model (research-only)."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib

from bot.research.strategy_simulator.simulator import VirtualTrade


@dataclass(frozen=True)
class ExecutionCostModel:
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    taker_fee: float = 0.0
    missed_fill_prob: float = 0.0
    max_spread: float | None = None
    name: str = "ideal"

    def validate(self) -> None:
        if self.entry_slippage < 0 or self.exit_slippage < 0:
            raise ValueError("slippage must be non-negative")
        if self.taker_fee < 0:
            raise ValueError("taker_fee must be non-negative")
        if not 0.0 <= self.missed_fill_prob < 1.0:
            raise ValueError("missed_fill_prob must be in [0, 1)")


IDEAL_COSTS = ExecutionCostModel(name="ideal")
BASE_COSTS = ExecutionCostModel(
    name="base",
    entry_slippage=0.005,
    exit_slippage=0.005,
    taker_fee=0.01,
    missed_fill_prob=0.05,
)
STRESS_COSTS = ExecutionCostModel(
    name="stress",
    entry_slippage=0.01,
    exit_slippage=0.01,
    taker_fee=0.02,
    missed_fill_prob=0.15,
    max_spread=0.02,
)

COST_SCENARIOS: tuple[ExecutionCostModel, ...] = (IDEAL_COSTS, BASE_COSTS, STRESS_COSTS)


def apply_cost_model(
    trades: list[VirtualTrade],
    model: ExecutionCostModel,
    *,
    seed: int = 42,
) -> list[VirtualTrade]:
    """Return adjusted trades; EV never improves vs ideal without costs."""
    model.validate()
    adjusted: list[VirtualTrade] = []
    for trade in trades:
        if model.max_spread is not None and trade.spread_at_entry is not None:
            if trade.spread_at_entry > model.max_spread:
                continue

        key = f"{seed}|{model.name}|{trade.market_slug}|{trade.entry_ts}"
        h = int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)
        if model.missed_fill_prob > 0 and (h % 1_000_000) < int(model.missed_fill_prob * 1_000_000):
            continue

        entry = trade.entry_price + model.entry_slippage
        exit_price = trade.exit_price - model.exit_slippage
        fee = model.taker_fee * (entry + exit_price)
        pnl = exit_price - entry - fee
        adjusted.append(replace(
            trade,
            entry_price=entry,
            exit_price=exit_price,
            pnl=pnl,
            won=pnl > 0,
        ))
    return adjusted
