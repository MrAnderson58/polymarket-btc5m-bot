"""Archetype simulation statistics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev

from bot.research.strategy_simulator.archetypes import ArchetypeStrategy
from bot.research.strategy_simulator.simulator import VirtualTrade


@dataclass
class ArchetypeStats:
    strategy: ArchetypeStrategy
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expected_value: float = 0.0
    max_drawdown: float = 0.0
    distinct_markets: int = 0

    @property
    def fingerprint(self) -> str:
        return self.strategy.fingerprint()


def compute_archetype_stats(strategy: ArchetypeStrategy, trades: list[VirtualTrade]) -> ArchetypeStats:
    if not trades:
        return ArchetypeStats(strategy=strategy)

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    return ArchetypeStats(
        strategy=strategy,
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(trades),
        profit_factor=pf,
        expected_value=mean(pnls),
        max_drawdown=_max_drawdown(pnls),
        distinct_markets=len({t.market_slug for t in trades}),
    )


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd
