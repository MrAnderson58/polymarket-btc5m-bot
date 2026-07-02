"""Stop loss and recovery analysis."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.analytics.intelligence_context import build_intelligence_context
from bot.analytics.recovery_analyzer import build_recovery_analyzer
from bot.config import ER_V2_STOP_LOSS_PCT
from bot.er_stop_loss_short_recovery_stats import fetch_stop_loss_short_recovery_stats
from bot.report.advanced import _simulate_stop_pnl
from bot.report.analytics import _metrics, fetch_v2_trades, trade_pnl
from bot.strategy_review.constants import STOP_ALTERNATIVES
from bot.strategy_review.metrics import (
    confidence_pct,
    max_drawdown,
    overfit_risk,
    p_value_vs_rest,
    stability_score,
    walk_forward_pass,
)


def _recovery_section(conn: sqlite3.Connection, closed: list) -> dict[str, Any]:
    short = fetch_stop_loss_short_recovery_stats(conn)
    ctx = build_intelligence_context(conn, closed)
    recovery = build_recovery_analyzer(closed, ctx=ctx)

    windows: list[dict[str, Any]] = [
        {
            "window_sec": 15,
            "recovery_rate": short.recovered_entry_15s_rate,
            "count": short.recovered_entry_15s_count,
        },
        {
            "window_sec": 30,
            "recovery_rate": short.recovered_entry_30s_rate,
            "count": short.recovered_entry_30s_count,
        },
        {
            "window_sec": 45,
            "recovery_rate": short.recovered_entry_45s_rate,
            "count": short.recovered_entry_45s_count,
        },
        {
            "window_sec": 60,
            "recovery_rate": short.recovered_entry_60s_rate,
            "count": short.recovered_entry_60s_count,
        },
    ]

    expiry_recovered = 0
    for t in recovery.get("trades", []):
        bid = t.get("max_bid_to_expiry")
        entry = t.get("entry")
        if bid is not None and entry is not None and bid >= float(entry):
            expiry_recovered += 1
    analyzed = recovery.get("analyzed_stops", 0)
    windows.append(
        {
            "window_sec": "expiry",
            "recovery_rate": expiry_recovered / analyzed if analyzed else 0.0,
            "count": expiry_recovered,
        }
    )

    return {
        "analyzed_stops": short.analyzed,
        "recovery_windows": windows,
        "alternative_holds": recovery.get("alternative_holds", []),
    }


def _alternative_stops(conn: sqlite3.Connection, closed: list) -> list[dict[str, Any]]:
    current_pnls = [trade_pnl(t) for t in closed]
    current_m = _metrics(current_pnls)
    rows: list[dict[str, Any]] = []

    for stop_pct in STOP_ALTERNATIVES:
        pnls = [_simulate_stop_pnl(conn, t, stop_pct) for t in closed]
        m = _metrics(pnls)
        losses = [p for p in pnls if p <= 0]
        wf = walk_forward_pass(pnls)
        rows.append(
            {
                "stop_pct": stop_pct,
                "profit_factor": round(m["profit_factor"], 3)
                if m["profit_factor"] != float("inf")
                else 99.9,
                "win_rate": round(m["win_rate"], 3),
                "net_profit": round(m["net_profit"], 2),
                "max_drawdown": max_drawdown(pnls),
                "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
                "recovery_rate": round(len([p for p in pnls if p > 0]) / len(pnls), 3)
                if pnls
                else 0.0,
                "trades": m["trades"],
                "avg_pnl": round(m["avg_pnl"], 3),
                "walk_forward": wf,
                "overfit_risk": overfit_risk(pnls),
                "p_value": round(p_value_vs_rest(pnls, current_pnls), 4),
                "sample_size": m["trades"],
                "confidence_pct": confidence_pct(
                    n=m["trades"],
                    p_value=p_value_vs_rest(pnls, current_pnls),
                    stability=stability_score(pnls),
                    walk_forward_ok=wf["passed"],
                    overfit=overfit_risk(pnls),
                ),
            }
        )
    return rows


def analyze_stop(
    conn: sqlite3.Connection,
    *,
    closed: list | None = None,
    current_stop_pct: float | None = None,
) -> dict[str, Any]:
    closed = closed if closed is not None else fetch_v2_trades(conn, closed_only=True)
    current = current_stop_pct if current_stop_pct is not None else ER_V2_STOP_LOSS_PCT

    recovery = _recovery_section(conn, closed)
    alternatives = _alternative_stops(conn, closed)

    current_row = min(alternatives, key=lambda r: abs(r["stop_pct"] - current))
    better = [
        r
        for r in alternatives
        if r["stop_pct"] != current
        and r["profit_factor"] > current_row["profit_factor"]
        and r["net_profit"] > current_row["net_profit"]
    ]
    best = max(better, key=lambda r: (r["profit_factor"], r["net_profit"]), default=None)

    recommendation: dict[str, Any] = {
        "action": "KEEP",
        "current_stop_pct": current,
        "recommended_stop_pct": current,
        "expected_pf_improvement_pct": 0.0,
        "confidence_pct": current_row["confidence_pct"],
        "risk": current_row["overfit_risk"],
        "reasons": [],
    }

    if best:
        pf_gain = (best["profit_factor"] - current_row["profit_factor"]) / max(
            current_row["profit_factor"], 0.01
        ) * 100
        recommendation.update(
            {
                "action": "CHANGE",
                "recommended_stop_pct": best["stop_pct"],
                "expected_pf_improvement_pct": round(pf_gain, 1),
                "confidence_pct": best["confidence_pct"],
                "risk": best["overfit_risk"],
                "candidate": best,
                "reasons": [
                    f"PF {best['profit_factor']:.2f} vs current {current_row['profit_factor']:.2f}",
                    f"walk-forward {'PASS' if best['walk_forward']['passed'] else 'FAIL'}",
                    f"overfit {best['overfit_risk']}",
                ],
            }
        )

    return {
        "current_stop_pct": current,
        "recovery": recovery,
        "alternatives": alternatives,
        "recommendation": recommendation,
    }
