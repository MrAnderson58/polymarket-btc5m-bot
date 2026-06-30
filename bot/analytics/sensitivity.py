"""Sensitivity analysis (Report v3 §24)."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.report.advanced import _simulate_stop_pnl, _simulate_trailing_pnl
from bot.report.analytics import ALT_STOP_PCTS, ENTRY_PRICES, TRAIL_ACTIVATION_OPTS, _metrics, trade_pnl


def _pct_change(base: float, new: float) -> float:
    if base == 0:
        return new
    return (new - base) / abs(base) * 100


def _sensitivity_label(max_swing: float) -> str:
    return "HIGH" if abs(max_swing) >= 15 else "LOW"


def build_sensitivity_analysis(
    conn: sqlite3.Connection,
    closed: list,
    optimizer: dict[str, Any],
) -> dict[str, Any]:
    cur = optimizer.get("current", {})
    current_entry = float(cur.get("entry", 0.40))
    current_stop = float(cur.get("stop_pct", -18.0))
    current_trail = float(cur.get("trailing_activation", 0.03))

    entry_curve: dict[float, float] = {}
    for price in ENTRY_PRICES:
        subset = [t for t in closed if float(t["entry_price"]) <= price + 0.005]
        entry_curve[price] = _metrics([trade_pnl(t) for t in subset])["profit_factor"]

    stop_curve: dict[float, float] = {}
    for stop_pct in ALT_STOP_PCTS:
        pnls = [_simulate_stop_pnl(conn, t, stop_pct) for t in closed]
        stop_curve[stop_pct] = _metrics(pnls)["profit_factor"]

    trail_curve: dict[float, float] = {}
    for activation in TRAIL_ACTIVATION_OPTS:
        pnls = [_simulate_trailing_pnl(conn, t, activation, 0.01) for t in closed]
        trail_curve[activation] = _metrics(pnls)["profit_factor"]

    def _steps(curve: dict[float, float], current: float) -> list[dict[str, Any]]:
        keys = sorted(curve.keys())
        if current not in curve:
            nearest = min(keys, key=lambda k: abs(k - current))
            base_pf = curve[nearest]
        else:
            base_pf = curve[current]
        steps = []
        idx = keys.index(min(keys, key=lambda k: abs(k - current)))
        for delta in (-1, 1):
            j = idx + delta
            if 0 <= j < len(keys):
                k = keys[j]
                pf = curve[k]
                steps.append(
                    {
                        "from": current,
                        "to": k,
                        "pf_change_pct": round(_pct_change(base_pf, pf), 1),
                        "profit_factor": pf,
                    }
                )
        return steps

    entry_steps = _steps(entry_curve, current_entry)
    stop_steps = _steps(stop_curve, current_stop)
    trail_steps = _steps(trail_curve, current_trail)

    entry_swings = [s["pf_change_pct"] for s in entry_steps]
    stop_swings = [s["pf_change_pct"] for s in stop_steps]
    trail_swings = [s["pf_change_pct"] for s in trail_steps]

    parameters = [
        {
            "parameter": "entry",
            "current": current_entry,
            "steps": entry_steps,
            "sensitivity": _sensitivity_label(max(entry_swings, key=abs) if entry_swings else 0),
            "max_swing_pct": max(entry_swings, key=abs) if entry_swings else 0,
        },
        {
            "parameter": "stop_loss",
            "current": abs(current_stop),
            "steps": [
                {**s, "to": abs(s["to"])} for s in stop_steps
            ],
            "sensitivity": _sensitivity_label(max(stop_swings, key=abs) if stop_swings else 0),
            "max_swing_pct": max(stop_swings, key=abs) if stop_swings else 0,
        },
        {
            "parameter": "trailing",
            "current": current_trail,
            "steps": trail_steps,
            "sensitivity": _sensitivity_label(max(trail_swings, key=abs) if trail_swings else 0),
            "max_swing_pct": max(trail_swings, key=abs) if trail_swings else 0,
        },
    ]

    return {"parameters": parameters}
