"""Equity simulation + portfolio metrics."""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from bot.research.market_events.signal_intelligence.portfolio_simulator_v1.sizing import (
    apply_costs,
    kelly_fraction,
    risk_amount,
    scale_trade_pnl,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import trade_metrics


def _seconds_span(trades: Sequence[dict[str, Any]]) -> float:
    ts = [int(t.get("opened_at") or 0) for t in trades if int(t.get("opened_at") or 0) > 0]
    if len(ts) < 2:
        return 365.25 * 86400
    return max(86400.0, float(max(ts) - min(ts)))


def simulate_equity(
    trades: Sequence[dict[str, Any]],
    *,
    capital: float,
    risk_model: str,
    risk_param: float,
    fee_bps: float = 10.0,
    slip_bps: float = 5.0,
    funding_bps: float = 2.0,
    store_curve: bool = False,
) -> dict[str, Any]:
    """
    Walk trades chronologically.

    Risk is sized off *initial* capital (non-compounding) so capital×risk
    grids stay comparable and Kelly does not explode CAGR.
    Research-only — no execution.
    """
    eq = float(capital)
    peak = eq
    max_dd = 0.0
    curve: list[float] = [eq]
    steps: list[dict[str, Any]] = []
    net_pnls: list[float] = []
    exposed = 0.0

    # Precompute kelly base from raw journal pnls if needed
    raw = []
    for t in trades:
        try:
            raw.append(float(t.get("pnl")))
        except Exception:
            pass
    base_kelly = kelly_fraction(raw) if risk_model == "kelly" else 0.0
    effective_param = (
        max(0.0, min(1.0, base_kelly * float(risk_param)))
        if risk_model == "kelly"
        else float(risk_param)
    )
    # Fixed risk budget from starting capital (sensitivity still varies capital/risk).
    ra = risk_amount(float(capital), model=risk_model, param=effective_param)

    for t in trades:
        try:
            gp = float(t.get("pnl"))
        except Exception:
            continue
        if ra <= 0 or eq <= 0:
            break
        exposed += ra
        scaled = scale_trade_pnl(gp, ra)
        net = apply_costs(scaled, ra, fee_bps=fee_bps, slip_bps=slip_bps, funding_bps=funding_bps)
        eq += net
        if eq < 0:
            eq = 0.0
        peak = max(peak, eq)
        dd = (eq - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
        net_pnls.append(net)
        curve.append(eq)
        if store_curve:
            steps.append({
                "trade_id": t.get("trade_id"),
                "pnl": round(net, 6),
                "equity": round(eq, 6),
            })

    return {
        "equity_curve": curve,
        "steps": steps,
        "net_pnls": net_pnls,
        "final_equity": round(eq, 4),
        "max_dd_frac": round(max_dd, 6),
        "effective_risk_param": effective_param,
        "exposure_total": round(exposed, 4),
    }


def portfolio_metrics(
    sim: dict[str, Any],
    trades: Sequence[dict[str, Any]],
    *,
    capital: float,
) -> dict[str, Any]:
    curve = [float(x) for x in (sim.get("equity_curve") or [capital])]
    pnls = [float(x) for x in (sim.get("net_pnls") or [])]
    met = trade_metrics(pnls)
    n = len(pnls)
    final = float(sim.get("final_equity") or capital)
    total_pnl = final - float(capital)
    ret_pct = (final / float(capital) - 1.0) * 100.0 if capital > 0 else 0.0

    # returns per trade for Sharpe/Sortino
    rets = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]
        if prev > 1e-12:
            rets.append((curve[i] - prev) / prev)
    sharpe = sortino = None
    if len(rets) >= 5:
        mu = float(np.mean(rets))
        sd = float(np.std(rets))
        sharpe = round(mu / sd * math.sqrt(len(rets)), 4) if sd > 1e-12 else None
        downside = [r for r in rets if r < 0]
        if downside:
            dsd = float(np.std(downside))
            sortino = round(mu / dsd * math.sqrt(len(rets)), 4) if dsd > 1e-12 else None
        else:
            sortino = sharpe

    max_dd = float(sim.get("max_dd_frac") or 0.0)
    # Ulcer: sqrt(mean(dd^2))
    peak = curve[0]
    dds = []
    for e in curve:
        peak = max(peak, e)
        dds.append(((e - peak) / peak) if peak > 0 else 0.0)
    ulcer = round(math.sqrt(sum(d * d for d in dds) / max(1, len(dds))), 6)

    years = _seconds_span(trades) / (365.25 * 86400)
    years = max(years, 1.0 / 365.25)
    cagr = None
    try:
        if capital > 0 and final > 0:
            ratio = final / capital
            # Short samples: linear annualization (geometric explodes on days of data).
            if years < 1.0:
                cagr = ((ratio - 1.0) / years) * 100.0
            elif ratio > 1e6:
                cagr = 1e6
            else:
                cagr = ((ratio) ** (1.0 / years) - 1.0) * 100.0
            if cagr != cagr or abs(cagr) == float("inf"):
                cagr = None
            else:
                cagr = round(float(max(-1e6, min(1e6, cagr))), 4)
    except OverflowError:
        cagr = None
    calmar = None
    if cagr is not None and abs(max_dd) > 1e-12:
        try:
            calmar = round((cagr / 100.0) / abs(max_dd), 4)
        except OverflowError:
            calmar = None
    mar = calmar
    recovery = None
    if abs(max_dd) > 1e-12 and total_pnl > 0:
        try:
            recovery = round(total_pnl / (abs(max_dd) * capital), 4)
        except OverflowError:
            recovery = None

    exposure = float(sim.get("exposure_total") or 0.0)
    avg_eq = float(np.mean(curve)) if curve else capital
    if avg_eq != avg_eq or abs(avg_eq) == float("inf"):
        avg_eq = capital
    exposure_pct = round(100.0 * exposure / max(avg_eq * max(n, 1), 1e-9), 4)

    # Cap insane equities for reporting
    if final != final or abs(final) == float("inf"):
        final = 0.0
    if total_pnl != total_pnl or abs(total_pnl) == float("inf"):
        total_pnl = 0.0
    if ret_pct != ret_pct or abs(ret_pct) == float("inf"):
        ret_pct = 0.0

    return {
        "n_trades": n,
        "pnl": round(float(total_pnl), 4) if abs(total_pnl) < 1e15 else 1e15,
        "ret_pct": round(float(ret_pct), 4) if abs(ret_pct) < 1e10 else 1e10,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "pf": met.get("pf"),
        "wr": met.get("wr"),
        "max_dd": round(max_dd * 100.0, 4),  # percent
        "ulcer": ulcer,
        "mar": mar,
        "recovery": recovery,
        "exposure": exposure_pct,
        "final_equity": round(float(final), 4) if abs(final) < 1e15 else 1e15,
    }


def ascii_equity(curve: Sequence[float], *, width: int = 56, height: int = 10) -> str:
    if not curve:
        return "(empty)"
    xs = [float(x) for x in curve]
    lo, hi = min(xs), max(xs)
    span = hi - lo if hi > lo else 1.0
    # downsample
    n = len(xs)
    step = max(1, n // width)
    pts = xs[::step][:width]
    rows = [[" "] * len(pts) for _ in range(height)]
    for i, v in enumerate(pts):
        y = int(round((v - lo) / span * (height - 1)))
        rows[height - 1 - y][i] = "*"
    lines = ["".join(r) for r in rows]
    lines.append(f"min={lo:.2f} max={hi:.2f} n={n}")
    return "\n".join(lines)


__all__ = ["ascii_equity", "portfolio_metrics", "simulate_equity"]
