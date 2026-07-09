"""Phase D.1.1 robust statistics, economic scenarios, and verdict helpers."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from bot.research.futures_agent.signal_outcome_constants import (
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    DATA_QUALITY_COMPLETE_THRESHOLD,
    SAMPLE_INSUFFICIENT,
)


FEE_BPS_SCENARIOS = (5, 10, 20)
SLIPPAGE_BPS_SCENARIOS = (5, 10, 20)
EXEC_DELAY_MIN_SCENARIOS = (0, 1, 3, 5)
TOP_TRIM_PCTS = (0.01, 0.02, 0.05)
WINSOR_TRIM_PCTS = (0.01, 0.02, 0.05)

# Verdict thresholds (fixed, not optimized on sample).
TOP10_CONCENTRATION_MAX = 0.45
TOP25_CONCENTRATION_MAX = 0.65
DEFAULT_COST_FEE_BPS = 10
DEFAULT_COST_SLIPPAGE_BPS = 10
FIXED_RISK_PCT = 0.01
MAX_RISK_POSITIONS = 5
MAX_ACCEPTABLE_RISK_DD_PCT = 35.0
RECENT_SIGNAL_WINDOWS = (100, 250, 500)


@dataclass
class RobustStats:
    n: int = 0
    arithmetic_mean: float = 0.0
    median: float = 0.0
    geometric_mean: float | None = None
    trimmed_means: dict[str, float] = field(default_factory=dict)
    winsorized_means: dict[str, float] = field(default_factory=dict)
    mean_after_top_trim: dict[str, float] = field(default_factory=dict)
    top_contribution_pct: dict[str, float] = field(default_factory=dict)
    bootstrap_mean_ci: tuple[float, float] | None = None


@dataclass
class CostScenarioResult:
    fee_bps: int
    slippage_bps: int
    mean_return: float
    median_return: float
    win_rate: float


@dataclass
class PortfolioSimResult:
    sizing_model: str
    final_equity: float
    max_drawdown_pct: float
    trade_count: int


@dataclass
class VerdictInputs:
    entered_n: int
    complete_data_pct: float
    headline_mean: float
    bootstrap_mean_ci: tuple[float, float] | None
    winsorized_5pct_mean: float | None
    top10_concentration: float | None
    top25_concentration: float | None
    cost_adjusted_mean: float | None
    risk_sized_max_dd: float | None
    oos_years_positive: int
    oos_years_total: int
    rolling_blocks_positive: int
    rolling_blocks_total: int
    rolling_3m_positive: int
    rolling_3m_total: int
    recent_250_mean: float | None
    p4_mean: float | None
    p5_mean: float | None
    p6_mean: float | None


def _sorted_values(values: Sequence[float]) -> list[float]:
    return sorted(values)


def trimmed_mean(values: Sequence[float], trim_pct: float) -> float | None:
    if not values:
        return None
    n = len(values)
    k = int(n * trim_pct)
    if k * 2 >= n:
        return statistics.mean(values)
    trimmed = _sorted_values(values)[k:n - k]
    return statistics.mean(trimmed) if trimmed else None


def winsorized_mean(values: Sequence[float], cap_pct: float) -> float | None:
    if not values:
        return None
    sorted_v = _sorted_values(values)
    n = len(sorted_v)
    k = max(1, int(n * cap_pct))
    lo = sorted_v[k - 1]
    hi = sorted_v[-k]
    capped = [min(hi, max(lo, v)) for v in values]
    return statistics.mean(capped)


def mean_after_removing_top_pct(values: Sequence[float], top_pct: float) -> float | None:
    if not values:
        return None
    n = len(values)
    k = max(1, int(math.ceil(n * top_pct)))
    trimmed = _sorted_values(values)[:-k] if k < n else []
    return statistics.mean(trimmed) if trimmed else None


def geometric_mean_return(values: Sequence[float]) -> float | None:
    """Per-trade compounded average (portfolio-compatible for equal weight)."""
    if not values:
        return None
    product = 1.0
    for r in values:
        product *= 1.0 + r / 100.0
    if product <= 0:
        return None
    return (product ** (1.0 / len(values)) - 1.0) * 100.0


def top_contribution_pct(values: Sequence[float], k: int) -> float | None:
    if not values:
        return None
    total = sum(values)
    if abs(total) < 1e-12:
        return 0.0
    top_sum = sum(_sorted_values(values)[-k:])
    return top_sum / total


def apply_round_trip_cost(return_pct: float, *, fee_bps: float, slippage_bps: float) -> float:
    """Subtract round-trip fee + slippage from unleveraged return (%)."""
    cost_pct = (fee_bps + slippage_bps) * 2 / 100.0
    return return_pct - cost_pct


def compute_robust_stats(values: Sequence[float]) -> RobustStats:
    import random

    rs = RobustStats()
    if not values:
        return rs
    rs.n = len(values)
    rs.arithmetic_mean = statistics.mean(values)
    rs.median = statistics.median(values)
    rs.geometric_mean = geometric_mean_return(values)
    for pct in WINSOR_TRIM_PCTS:
        key = f"{int(pct * 100)}pct"
        wm = winsorized_mean(values, pct)
        if wm is not None:
            rs.winsorized_means[key] = wm
        tm = trimmed_mean(values, pct)
        if tm is not None:
            rs.trimmed_means[key] = tm
        ma = mean_after_removing_top_pct(values, pct)
        if ma is not None:
            rs.mean_after_top_trim[key] = ma
    for k in (1, 5, 10, 25):
        c = top_contribution_pct(values, k)
        if c is not None:
            rs.top_contribution_pct[f"top_{k}"] = c
    if len(values) >= 5:
        rng = random.Random(BOOTSTRAP_SEED)
        means: list[float] = []
        for _ in range(BOOTSTRAP_SAMPLES):
            sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
            means.append(statistics.mean(sample))
        means.sort()
        lo = means[int(0.025 * len(means))]
        hi = means[int(0.975 * len(means)) - 1]
        rs.bootstrap_mean_ci = (lo, hi)
    return rs


def equal_risk_return(
    return_pct: float,
    *,
    entry_price: float | None,
    stop_price: float | None,
    direction: str,
    risk_pct: float = FIXED_RISK_PCT,
    max_position_pct: float = 0.25,
) -> float | None:
    """Scale return by stop distance for equal-risk sizing (capped)."""
    if entry_price is None or stop_price is None or entry_price <= 0:
        return None
    if direction == "LONG":
        stop_dist_pct = abs(entry_price - stop_price) / entry_price * 100.0
    else:
        stop_dist_pct = abs(stop_price - entry_price) / entry_price * 100.0
    if stop_dist_pct < 0.05:
        return None
    position_frac = min(risk_pct / (stop_dist_pct / 100.0), max_position_pct)
    return return_pct * position_frac / risk_pct


def simulate_portfolio_equity(
    trades: Sequence[dict[str, Any]],
    *,
    sizing: str = "fixed_risk",
    risk_pct: float = FIXED_RISK_PCT,
    max_positions: int = MAX_RISK_POSITIONS,
    initial_equity: float = 1.0,
) -> PortfolioSimResult:
    """Sequential portfolio equity with explicit sizing (not naive leverage)."""
    equity = initial_equity
    peak = equity
    max_dd = 0.0
    open_risk = 0.0
    count = 0
    for t in trades:
        ret = t.get("return_pct")
        if ret is None:
            continue
        if sizing == "equal_weight":
            pnl_frac = (ret / 100.0) * risk_pct
        else:
            scaled = equal_risk_return(
                ret,
                entry_price=t.get("entry_price"),
                stop_price=t.get("stop_price"),
                direction=str(t.get("direction", "LONG")),
                risk_pct=risk_pct,
            )
            if scaled is None:
                pnl_frac = (ret / 100.0) * risk_pct
            else:
                pnl_frac = scaled / 100.0 * risk_pct
        if open_risk + risk_pct > max_positions * risk_pct:
            continue
        open_risk = min(open_risk + risk_pct, max_positions * risk_pct)
        equity *= 1.0 + pnl_frac
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0 if peak > 0 else 0.0)
        open_risk = max(0.0, open_risk - risk_pct)
        count += 1
    return PortfolioSimResult(
        sizing_model=sizing,
        final_equity=equity,
        max_drawdown_pct=max_dd,
        trade_count=count,
    )


def compute_cost_scenarios(returns: Sequence[float]) -> list[CostScenarioResult]:
    results: list[CostScenarioResult] = []
    for fee in FEE_BPS_SCENARIOS:
        for slip in SLIPPAGE_BPS_SCENARIOS:
            adjusted = [apply_round_trip_cost(r, fee_bps=fee, slippage_bps=slip) for r in returns]
            wins = [r for r in adjusted if r > 0]
            results.append(CostScenarioResult(
                fee_bps=fee,
                slippage_bps=slip,
                mean_return=statistics.mean(adjusted) if adjusted else 0.0,
                median_return=statistics.median(adjusted) if adjusted else 0.0,
                win_rate=len(wins) / len(adjusted) if adjusted else 0.0,
            ))
    return results


def compute_research_verdict(inp: VerdictInputs) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if inp.complete_data_pct < DATA_QUALITY_COMPLETE_THRESHOLD:
        return "INSUFFICIENT_DATA", [
            f"Data completeness {inp.complete_data_pct:.1%} < {DATA_QUALITY_COMPLETE_THRESHOLD:.0%}",
        ]
    if inp.entered_n < SAMPLE_INSUFFICIENT:
        return "INSUFFICIENT_DATA", [
            f"Entered N={inp.entered_n} < {SAMPLE_INSUFFICIENT}",
        ]

    bootstrap_lb = inp.bootstrap_mean_ci[0] if inp.bootstrap_mean_ci else None
    robust_mean_ok = (
        (bootstrap_lb is not None and bootstrap_lb > 0)
        or (inp.winsorized_5pct_mean is not None and inp.winsorized_5pct_mean > 0)
    )
    if bootstrap_lb is not None and bootstrap_lb <= 0:
        reasons.append(f"Bootstrap mean lower bound {bootstrap_lb:.3f}% <= 0")
    if inp.winsorized_5pct_mean is not None and inp.winsorized_5pct_mean <= 0:
        reasons.append(f"Winsorized 5% mean {inp.winsorized_5pct_mean:.3f}% <= 0")

    oos_ok = (
        inp.oos_years_total >= 2
        and inp.oos_years_positive >= max(2, (inp.oos_years_total * 2 + 2) // 3)
    )
    if not oos_ok:
        reasons.append(
            f"OOS years positive {inp.oos_years_positive}/{inp.oos_years_total} below 2/3 threshold",
        )

    rolling_ok = (
        inp.rolling_blocks_total >= 3
        and inp.rolling_blocks_positive >= (inp.rolling_blocks_total * 2 + 2) // 3
    )
    if not rolling_ok:
        reasons.append(
            f"Rolling 25% blocks positive {inp.rolling_blocks_positive}/{inp.rolling_blocks_total}",
        )

    rolling_3m_ok = (
        inp.rolling_3m_total >= 2
        and inp.rolling_3m_positive >= (inp.rolling_3m_total + 1) // 2
    )
    if not rolling_3m_ok:
        reasons.append(
            f"Rolling 3-month windows positive {inp.rolling_3m_positive}/{inp.rolling_3m_total}",
        )

    concentration_ok = True
    if inp.top10_concentration is not None and inp.top10_concentration > TOP10_CONCENTRATION_MAX:
        concentration_ok = False
        reasons.append(
            f"Top-10 trade concentration {inp.top10_concentration:.1%} > {TOP10_CONCENTRATION_MAX:.0%}",
        )
    if inp.top25_concentration is not None and inp.top25_concentration > TOP25_CONCENTRATION_MAX:
        concentration_ok = False
        reasons.append(
            f"Top-25 trade concentration {inp.top25_concentration:.1%} > {TOP25_CONCENTRATION_MAX:.0%}",
        )

    cost_ok = inp.cost_adjusted_mean is not None and inp.cost_adjusted_mean > 0
    if not cost_ok:
        reasons.append(
            f"Mean after {DEFAULT_COST_FEE_BPS}+{DEFAULT_COST_SLIPPAGE_BPS} bps costs not positive",
        )

    dd_ok = inp.risk_sized_max_dd is None or inp.risk_sized_max_dd <= MAX_ACCEPTABLE_RISK_DD_PCT
    if not dd_ok:
        reasons.append(
            f"Fixed-risk portfolio max DD {inp.risk_sized_max_dd:.1f}% > {MAX_ACCEPTABLE_RISK_DD_PCT:.0f}%",
        )

    recent_ok = inp.recent_250_mean is None or inp.recent_250_mean > 0
    if not recent_ok:
        reasons.append(f"Recent 250-signal mean {inp.recent_250_mean:.3f}% <= 0")

    horizon_policies_negative = sum(
        1 for m in (inp.p4_mean, inp.p5_mean, inp.p6_mean)
        if m is not None and m < 0
    )
    if horizon_policies_negative >= 2:
        reasons.append("Majority of fixed-horizon policies P4-P6 negative")

    persistent_checks = [
        robust_mean_ok,
        oos_ok,
        rolling_ok,
        concentration_ok,
        cost_ok,
        dd_ok,
        recent_ok,
        inp.headline_mean > 0,
    ]
    persistent_pass = sum(persistent_checks)

    conditional_checks = [
        inp.headline_mean > 0,
        rolling_ok or oos_ok,
        (inp.recent_250_mean or 0) > -1.0,
    ]

    if persistent_pass >= 7 and robust_mean_ok and concentration_ok and cost_ok:
        return "SOURCE_HAS_PERSISTENT_EDGE", [
            "Robust mean positive with OOS/rolling stability, acceptable concentration and costs",
        ]
    if sum(conditional_checks) >= 2 and inp.headline_mean > 0:
        return "CONDITIONAL_EDGE", reasons or ["Positive headline but failed persistent-edge bar"]
    return "NOT_STABLE", reasons or ["Conservative mean not robust across regimes"]


def compute_missed_entry_sensitivity(
    trades: Sequence[dict[str, Any]],
    *,
    delay_min: int,
) -> dict[str, Any]:
    """Market-entry signals that would likely miss after execution delay."""
    market = [t for t in trades if t.get("entry_mode") == "MARKET_ENTRY"]
    if not market:
        return {"market_entry_n": 0, "missed_after_delay": 0, "miss_rate": 0.0}
    missed = 0
    for t in market:
        entry = t.get("entry_price")
        stop = t.get("stop_price")
        if entry and stop:
            dist = abs(float(entry) - float(stop)) / float(entry)
            if dist < (delay_min * 0.0005):
                missed += 1
    return {
        "market_entry_n": len(market),
        "missed_after_delay": missed,
        "miss_rate": missed / len(market) if market else 0.0,
        "delay_min": delay_min,
    }


def apply_execution_delay_haircut(return_pct: float, delay_min: int) -> float:
    """Conservative delay haircut when candle re-entry unavailable (bps per minute)."""
    return return_pct - delay_min * 2.0


def compute_delay_scenarios(returns: Sequence[float], delays_min: Sequence[int] = EXEC_DELAY_MIN_SCENARIOS) -> dict[str, float]:
    return {
        f"delay_{d}m_mean": statistics.mean([apply_execution_delay_haircut(r, d) for r in returns])
        if returns else 0.0
        for d in delays_min
    }
