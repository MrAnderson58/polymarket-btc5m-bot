"""Monte Carlo, stress, and sensitivity for Portfolio Simulator V1."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.simulate import (
    portfolio_metrics,
    simulate_equity,
)


def monte_carlo(
    trades: Sequence[dict[str, Any]],
    *,
    capital: float,
    risk_model: str,
    risk_param: float,
    n_sims: int = 1000,
    seed: int = 42,
    fee_bps: float = 10.0,
    slip_bps: float = 5.0,
    funding_bps: float = 2.0,
) -> dict[str, Any]:
    """
    Fast bootstrap of sized trade PnLs (one sizing pass, then resample+shuffle).
    Research approximation of path order / sampling uncertainty.
    """
    if not trades:
        return {"n_sims": 0, "finals": [], "ci": {}}
    base = simulate_equity(
        trades,
        capital=capital,
        risk_model=risk_model,
        risk_param=risk_param,
        fee_bps=fee_bps,
        slip_bps=slip_bps,
        funding_bps=funding_bps,
        store_curve=False,
    )
    pnls = np.array(base.get("net_pnls") or [], dtype=float)
    n = len(pnls)
    if n == 0:
        return {"n_sims": 0, "final_mean": capital, "ci_5": capital, "ci_95": capital}

    rng = np.random.default_rng(seed)
    sims = int(n_sims)
    # Vectorized bootstrap: (sims, n)
    idx = rng.integers(0, n, size=(sims, n))
    samples = pnls[idx]
    # Shuffle each row for order path
    for i in range(sims):
        rng.shuffle(samples[i])
    finals = float(capital) + np.cumsum(samples, axis=1)[:, -1]
    # MaxDD approx on cumsum equity
    equity = float(capital) + np.cumsum(samples, axis=1)
    peaks = np.maximum.accumulate(equity, axis=1)
    dds = (equity - peaks) / np.maximum(peaks, 1e-12)
    maxdds = dds.min(axis=1)

    return {
        "n_sims": sims,
        "final_mean": round(float(np.mean(finals)), 4),
        "final_median": round(float(np.median(finals)), 4),
        "ci_5": round(float(np.percentile(finals, 5)), 4),
        "ci_95": round(float(np.percentile(finals, 95)), 4),
        "max_dd_mean_pct": round(float(np.mean(maxdds)) * 100.0, 4),
        "max_dd_ci_95_pct": round(float(np.percentile(maxdds, 95)) * 100.0, 4),
        "p_profit": round(float(np.mean(finals > capital)), 4),
        "method": "bootstrap_sized_pnls",
    }


def stress_tests(
    trades: Sequence[dict[str, Any]],
    *,
    capital: float,
    risk_model: str,
    risk_param: float,
) -> list[dict[str, Any]]:
    xs = list(trades)
    ranked = sorted(xs, key=lambda t: float(t.get("pnl") or 0), reverse=True)

    def _run(label: str, subset: list[dict[str, Any]], **kw) -> dict[str, Any]:
        sim = simulate_equity(
            subset,
            capital=capital,
            risk_model=risk_model,
            risk_param=risk_param,
            fee_bps=kw.get("fee_bps", 10.0),
            slip_bps=kw.get("slip_bps", 5.0),
            funding_bps=kw.get("funding_bps", 2.0),
        )
        met = portfolio_metrics(sim, subset, capital=capital)
        return {"stress": label, **met}

    out = []
    drop10 = ranked[10:] if len(ranked) > 10 else []
    drop50 = ranked[50:] if len(ranked) > 50 else []
    out.append(_run("lose_best_10", drop10))
    out.append(_run("lose_best_50", drop50))
    rng = np.random.default_rng(7)
    keep_n = max(0, int(len(xs) * 0.9))
    if xs and keep_n:
        pick = rng.choice(len(xs), size=keep_n, replace=False)
        subset = [xs[int(i)] for i in sorted(pick)]
        out.append(_run("lose_random_10pct", subset))
    out.append(_run("double_fees", xs, fee_bps=20.0, slip_bps=10.0, funding_bps=4.0))
    half = [{**t, "pnl": float(t.get("pnl") or 0) * 0.5} for t in xs]
    out.append(_run("half_ev", half))
    return out


def sensitivity_grid(
    trades: Sequence[dict[str, Any]],
    *,
    capitals: Sequence[float],
    risk_specs: Sequence[tuple[str, float, str]],
) -> list[dict[str, Any]]:
    rows = []
    for cap in capitals:
        for model, param, label in risk_specs:
            sim = simulate_equity(
                trades, capital=float(cap), risk_model=model, risk_param=float(param)
            )
            met = portfolio_metrics(sim, trades, capital=float(cap))
            rows.append({
                "capital": float(cap),
                "risk_model": model,
                "risk_param": float(param),
                "risk_label": label,
                **met,
            })
    return rows


__all__ = ["monte_carlo", "sensitivity_grid", "stress_tests"]
