"""Section 39 — capital simulator in USD."""

from __future__ import annotations

from typing import Any

import bot.config as config
from bot.report.analytics import trade_pnl


def _trade_usdc_pnl(trade, stake: float) -> float:
    if trade["pnl_usdc"] is not None:
        scale = stake / config.EARLY_REVERSION_POSITION_SIZE_USDC
        return float(trade["pnl_usdc"]) * scale
    return trade_pnl(trade) / 100.0 * stake


def _simulate(closed: list, starting: float, stake: float) -> dict[str, Any]:
    equity = starting
    curve = [round(equity, 2)]
    peak = starting
    max_dd = 0.0

    for trade in closed:
        equity += _trade_usdc_pnl(trade, stake)
        equity = max(0.0, equity)
        curve.append(round(equity, 2))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "starting_usd": starting,
        "ending_usd": round(equity, 2),
        "return_pct": round((equity - starting) / starting * 100, 1) if starting else 0,
        "max_drawdown_usd": round(max_dd, 2),
        "curve_sample": curve[:12] + (["…"] if len(curve) > 24 else []) + curve[-8:],
        "curve_points": len(curve),
    }


def build_capital_simulator(
    closed: list,
    report: dict[str, Any],
) -> dict[str, Any]:
    del report
    stake = config.EARLY_REVERSION_POSITION_SIZE_USDC
    starters = (100, 1000, 5000, 10000, 50000)
    scenarios = [_simulate(closed, s, stake) for s in starters]

    primary = scenarios[0]
    path = primary["curve_sample"]
    path_str = " → ".join(f"${p}" if isinstance(p, (int, float)) else p for p in path[:10])

    return {
        "position_size_usdc": stake,
        "risk_note": "Fixed stake per trade; no compounding",
        "primary_path_100usd": path_str,
        "scenarios": scenarios,
    }
