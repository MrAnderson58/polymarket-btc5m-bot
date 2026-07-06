"""Aggregate simulation metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev

from bot.research.strategy_simulator.simulator import VirtualTrade
from bot.research.strategy_simulator.strategies import Strategy


@dataclass
class SimulationStats:
    strategy: Strategy
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    profit_factor: float = 0.0
    expected_value: float = 0.0
    average_holding_seconds: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float | None = None
    longest_losing_streak: int = 0
    longest_winning_streak: int = 0

    @property
    def fingerprint(self) -> str:
        return self.strategy.fingerprint()


def compute_stats(strategy: Strategy, trades: list[VirtualTrade]) -> SimulationStats:
    if not trades:
        return SimulationStats(strategy=strategy)

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    std = pstdev(pnls) if len(pnls) > 1 else 0.0
    sharpe = (mean(pnls) / std * math.sqrt(len(pnls))) if std > 1e-12 else None

    return SimulationStats(
        strategy=strategy,
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / len(trades),
        average_win=mean(wins) if wins else 0.0,
        average_loss=mean(losses) if losses else 0.0,
        profit_factor=pf,
        expected_value=mean(pnls),
        average_holding_seconds=mean(t.holding_seconds for t in trades),
        max_drawdown=_max_drawdown(pnls),
        sharpe=sharpe,
        longest_losing_streak=_longest_streak(pnls, win=False),
        longest_winning_streak=_longest_streak(pnls, win=True),
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


def _longest_streak(pnls: list[float], *, win: bool) -> int:
    best = cur = 0
    for p in pnls:
        hit = p > 0 if win else p <= 0
        if hit:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
