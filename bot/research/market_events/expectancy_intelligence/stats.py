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


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _pnl_epsilon() -> float:
    return 1e-9


def classify_pnl(pnl: float) -> str:
    if pnl > _pnl_epsilon():
        return "win"
    if pnl < -_pnl_epsilon():
        return "loss"
    return "breakeven"


def mean_stderr(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    return stdev(values) / math.sqrt(n)


def ci95_mean(values: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    """Returns (mean, low, high) 95% CI using normal approx (1.96 × SE)."""
    if not values:
        return None, None, None
    m = mean(values)
    se = mean_stderr(values)
    if se is None:
        return round(m, 4), None, None
    margin = 1.96 * se
    return round(m, 4), round(m - margin, 4), round(m + margin, 4)


def ci95_win_rate(wins: int, n: int) -> tuple[float, float | None, float | None]:
    """Win rate % with 95% CI (normal approx on proportion)."""
    if n <= 0:
        return 0.0, None, None
    p = wins / n
    wr = round(100.0 * p, 1)
    if n < 2:
        return wr, None, None
    se = math.sqrt(p * (1.0 - p) / n)
    margin = 1.96 * se * 100.0
    return wr, round(wr - margin, 1), round(wr + margin, 1)


def reliability_rank_score(expectancy: float, n: int) -> float:
    """EV × log(n) — favors stable sample size over rare spikes."""
    if n <= 1:
        return 0.0
    return round(expectancy * math.log(n), 4)


def trade_outcome_stats(
    trades: list[dict[str, Any]],
    *,
    min_reliable_n: int = 30,
) -> dict[str, Any]:
    """Aggregate block for a list of trades with pnl_pct."""
    pnls = [float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") is not None]
    n = len(pnls)
    empty: dict[str, Any] = {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": 0.0,
        "win_rate_se": None,
        "win_rate_ci95_low": None,
        "win_rate_ci95_high": None,
        "avg_pnl": 0.0,
        "avg_pnl_se": None,
        "avg_pnl_ci95_low": None,
        "avg_pnl_ci95_high": None,
        "profit_factor": None,
        "expectancy": 0.0,
        "expectancy_se": None,
        "expectancy_ci95_low": None,
        "expectancy_ci95_high": None,
        "median_pnl": None,
        "reliable": False,
        "rank_score": 0.0,
    }
    if n == 0:
        return empty
    wins = sum(1 for p in pnls if classify_pnl(p) == "win")
    losses = sum(1 for p in pnls if classify_pnl(p) == "loss")
    breakeven = sum(1 for p in pnls if classify_pnl(p) == "breakeven")
    pf = profit_factor_from_pnls(pnls)
    med = median(pnls)
    wr, wr_lo, wr_hi = ci95_win_rate(wins, n)
    _, ev_lo, ev_hi = ci95_mean(pnls)
    se = mean_stderr(pnls)
    ev = expectancy_from_pnls(pnls)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": wr,
        "win_rate_se": round(math.sqrt((wins / n) * (1 - wins / n) / n) * 100.0, 2) if n >= 2 else None,
        "win_rate_ci95_low": wr_lo,
        "win_rate_ci95_high": wr_hi,
        "avg_pnl": round(mean(pnls), 4),
        "avg_pnl_se": round(se, 4) if se is not None else None,
        "avg_pnl_ci95_low": ev_lo,
        "avg_pnl_ci95_high": ev_hi,
        "profit_factor": pf,
        "expectancy": ev,
        "expectancy_se": round(se, 4) if se is not None else None,
        "expectancy_ci95_low": ev_lo,
        "expectancy_ci95_high": ev_hi,
        "median_pnl": round(med, 4) if med is not None else None,
        "reliable": n >= min_reliable_n,
        "rank_score": reliability_rank_score(ev, n),
    }


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
