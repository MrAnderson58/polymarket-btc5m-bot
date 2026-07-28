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


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def expectancy_from_pnls(pnls: Sequence[float]) -> float:
    """Per-trade expectancy = mean PnL %."""
    return round(mean(pnls), 4) if pnls else 0.0


def trade_outcome_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate block for a list of trades with pnl_pct."""
    pnls = [float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") is not None]
    n = len(pnls)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "avg_pnl": 0.0, "profit_factor": None, "expectancy": 0.0}
    wins = sum(1 for p in pnls if p > 0)
    pf = profit_factor_from_pnls(pnls)
    med = median(pnls)
    return {
        "trades": n,
        "win_rate": round(100.0 * wins / n, 1),
        "avg_pnl": round(mean(pnls), 4),
        "profit_factor": pf,
        "expectancy": expectancy_from_pnls(pnls),
        "median_pnl": round(med, 4) if med is not None else None,
    }


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
