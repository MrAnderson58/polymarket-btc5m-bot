"""Pure-Python experiment statistics (extensible; no ML libs)."""

from __future__ import annotations

import math
from typing import Any, Sequence

from bot.research.market_events.expectancy_intelligence.stats import (
    ci95_mean,
    mean,
    stdev,
    trade_outcome_stats,
)


def pnls_of(trades: list[dict[str, Any]]) -> list[float]:
    return [float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") is not None]


def mfe_mae(trades: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    mfes = [float(t["mfe_pct"]) for t in trades if t.get("mfe_pct") is not None]
    maes = [float(t["mae_pct"]) for t in trades if t.get("mae_pct") is not None]
    return (
        round(mean(mfes), 4) if mfes else None,
        round(mean(maes), 4) if maes else None,
    )


def metrics_block(trades: list[dict[str, Any]], *, min_reliable_n: int = 30) -> dict[str, Any]:
    s = trade_outcome_stats(trades, min_reliable_n=min_reliable_n)
    mfe, mae = mfe_mae(trades)
    pf = s["profit_factor"]
    if pf == float("inf"):
        pf = None
    return {
        "n": int(s["trades"]),
        "ev": float(s["expectancy"]),
        "pf": None if pf is None else float(pf),
        "wr": float(s["win_rate"]),
        "mfe": mfe,
        "mae": mae,
        "reliable": bool(s["reliable"]),
        "ev_ci95_low": s.get("expectancy_ci95_low"),
        "ev_ci95_high": s.get("expectancy_ci95_high"),
    }


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Cohen's d for independent samples (a vs b). Positive → a mean higher than b."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = mean(a), mean(b)
    sa, sb = stdev(a), stdev(b)
    # pooled SD
    na, nb = len(a), len(b)
    pooled_var = ((na - 1) * sa * sa + (nb - 1) * sb * sb) / max(1, na + nb - 2)
    if pooled_var <= 0:
        return 0.0 if ma == mb else None
    return round((ma - mb) / math.sqrt(pooled_var), 4)


def welch_t_pvalue(a: Sequence[float], b: Sequence[float]) -> float | None:
    """
    Two-sided Welch t-test p-value via normal approximation of t
    (good enough for large n; None when underpowered).
    Architecture hook for richer scipy later.
    """
    if len(a) < 3 or len(b) < 3:
        return None
    ma, mb = mean(a), mean(b)
    sa, sb = stdev(a), stdev(b)
    na, nb = len(a), len(b)
    se2 = (sa * sa) / na + (sb * sb) / nb
    if se2 <= 0:
        return 1.0 if abs(ma - mb) < 1e-12 else 0.0
    t = abs(ma - mb) / math.sqrt(se2)
    # Normal approx two-sided survival: erfc(t / sqrt(2))
    p = math.erfc(t / math.sqrt(2.0))
    return round(max(0.0, min(1.0, p)), 6)


def format_ci(low: float | None, high: float | None) -> str | None:
    if low is None or high is None:
        return None
    return f"[{low:.4f}, {high:.4f}]"


def compare_groups(
    before_trades: list[dict[str, Any]],
    after_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """Baseline vs treatment metrics + effect size / p-value / CI."""
    before = metrics_block(before_trades)
    after = metrics_block(after_trades)
    a_pnls = pnls_of(after_trades)
    b_pnls = pnls_of(before_trades)
    # For filter experiments: effect = after vs before (baseline all)
    # Cohen's d of after vs complement is often clearer; here after vs before set.
    d = cohens_d(a_pnls, b_pnls) if a_pnls and b_pnls else None
    p = welch_t_pvalue(a_pnls, b_pnls) if a_pnls and b_pnls else None
    _, ci_lo, ci_hi = ci95_mean(a_pnls) if a_pnls else (None, None, None)
    def _delta(x: float | None, y: float | None) -> float | None:
        if x is None or y is None:
            return None
        return round(x - y, 4)

    return {
        "before": before,
        "after": after,
        "delta_ev": _delta(after["ev"], before["ev"]),
        "delta_pf": _delta(after["pf"], before["pf"]),
        "delta_wr": _delta(after["wr"], before["wr"]),
        "effect_size": d,
        "p_value": p,
        "confidence_interval": format_ci(ci_lo, ci_hi),
        "dataset_size": int(before["n"]),
    }
