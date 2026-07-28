"""Pure-Python stats for Expectancy Intelligence (no ML libraries)."""

from __future__ import annotations

import math
from typing import Any, Sequence


def safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return round(cov / math.sqrt(vx * vy), 4)


def mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def profit_factor_from_pnls(pnls: Sequence[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return round(wins / abs(losses), 4)


def avg_winner_loser(pnls: Sequence[float]) -> tuple[float, float]:
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    avg_win = round(mean(winners), 4) if winners else 0.0
    avg_loss = round(mean(losers), 4) if losers else 0.0
    return avg_win, avg_loss
