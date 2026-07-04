"""Lightweight metrics for evolution shadow (no report/execution imports)."""

from __future__ import annotations

from typing import Any


def trade_pnl_percent(trade: Any) -> float:
    if trade["pnl_percent"] is not None:
        return float(trade["pnl_percent"])
    entry = float(trade["entry_price"])
    exit_price = float(trade["exit_price"] if trade["exit_price"] is not None else entry)
    return (exit_price - entry) / entry * 100.0


def lane_metrics(pnls: list[float]) -> dict[str, float]:
    if not pnls:
        return {"pf": 0.0, "wr": 0.0, "dd": 0.0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    if not total_loss:
        pf = 0.0 if not total_profit else 99.0
    else:
        pf = total_profit / total_loss
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return {
        "pf": round(float(pf), 3),
        "wr": round(len(wins) / len(pnls) * 100.0, 2),
        "dd": round(max_dd, 2),
    }
