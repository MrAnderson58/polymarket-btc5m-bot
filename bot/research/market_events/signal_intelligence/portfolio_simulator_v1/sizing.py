"""Sizing + costs for Portfolio Simulator V1."""

from __future__ import annotations

from typing import Any, Sequence

# Paper trades assumed sized around this notional USD.
UNIT_NOTIONAL = 100.0

DEFAULT_FEE_BPS = 10.0
DEFAULT_SLIP_BPS = 5.0
DEFAULT_FUNDING_BPS = 2.0

CAPITALS = (1000.0, 5000.0, 10000.0, 50000.0, 100000.0)
FIXED_RISKS = (0.01, 0.02, 0.05)
KELLY_FRACS = (0.25, 0.5, 1.0)


def kelly_fraction(pnls: Sequence[float]) -> float:
    """Research Kelly from win rate + payoff (capped 0..1)."""
    xs = [float(p) for p in pnls if p is not None]
    if not xs:
        return 0.0
    wins = [p for p in xs if p > 0]
    losses = [p for p in xs if p < 0]
    if not wins or not losses:
        return 0.25 if wins else 0.0
    wr = len(wins) / len(xs)
    avg_w = sum(wins) / len(wins)
    avg_l = abs(sum(losses) / len(losses))
    if avg_l < 1e-12:
        return 0.25
    b = avg_w / avg_l
    f = wr - (1.0 - wr) / b
    return max(0.0, min(1.0, float(f)))


def risk_amount(equity: float, *, model: str, param: float) -> float:
    eq = max(0.0, float(equity))
    p = float(param)
    if model == "fixed":
        return eq * p
    if model == "kelly":
        return eq * p  # param already scaled kelly fraction
    return eq * 0.01


def apply_costs(
    gross_pnl: float,
    risk_amt: float,
    *,
    fee_bps: float = DEFAULT_FEE_BPS,
    slip_bps: float = DEFAULT_SLIP_BPS,
    funding_bps: float = DEFAULT_FUNDING_BPS,
) -> float:
    """Subtract proportional costs from trade PnL (research approximation)."""
    notional = max(risk_amt, 1e-9)
    cost = notional * (fee_bps + slip_bps + funding_bps) / 10_000.0
    return float(gross_pnl) - cost


def scale_trade_pnl(trade_pnl: float, risk_amt: float, *, unit: float = UNIT_NOTIONAL) -> float:
    """Map journal USD pnl (unit notional) onto current risk budget."""
    u = unit if unit > 1e-12 else 1.0
    return float(trade_pnl) * (float(risk_amt) / u)


def parse_risk_specs() -> list[tuple[str, float, str]]:
    """Return (model, param, label)."""
    out: list[tuple[str, float, str]] = []
    for r in FIXED_RISKS:
        out.append(("fixed", r, f"fixed_{int(r*100)}pct"))
    for k in KELLY_FRACS:
        out.append(("kelly", k, f"kelly_{k}"))
    return out


__all__ = [
    "CAPITALS",
    "DEFAULT_FEE_BPS",
    "DEFAULT_FUNDING_BPS",
    "DEFAULT_SLIP_BPS",
    "FIXED_RISKS",
    "KELLY_FRACS",
    "UNIT_NOTIONAL",
    "apply_costs",
    "kelly_fraction",
    "parse_risk_specs",
    "risk_amount",
    "scale_trade_pnl",
]
