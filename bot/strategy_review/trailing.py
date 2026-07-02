"""Trailing stop parameter analysis."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.config import TRAILING_ACTIVATION_PROFIT, TRAILING_OFFSET
from bot.report.advanced import _simulate_trailing_pnl
from bot.report.analytics import _metrics, fetch_v2_trades, trade_pnl
from bot.strategy_review.constants import TRAIL_ACTIVATION_OPTS, TRAIL_DISTANCE_OPTS
from bot.strategy_review.metrics import confidence_pct, max_drawdown, overfit_risk, p_value_vs_rest, stability_score, walk_forward_pass


def analyze_trailing(
    conn: sqlite3.Connection,
    *,
    closed: list | None = None,
    current_activation: float | None = None,
    current_distance: float | None = None,
) -> dict[str, Any]:
    closed = closed if closed is not None else fetch_v2_trades(conn, closed_only=True)
    cur_act = current_activation if current_activation is not None else TRAILING_ACTIVATION_PROFIT
    cur_dist = current_distance if current_distance is not None else TRAILING_OFFSET
    baseline_pnls = [trade_pnl(t) for t in closed]

    combos: list[dict[str, Any]] = []
    for activation in TRAIL_ACTIVATION_OPTS:
        for distance in TRAIL_DISTANCE_OPTS:
            pnls = [
                _simulate_trailing_pnl(conn, t, activation, distance) for t in closed
            ]
            m = _metrics(pnls)
            wf = walk_forward_pass(pnls)
            ovr = overfit_risk(pnls)
            combos.append(
                {
                    "activation": activation,
                    "distance": distance,
                    "profit_factor": round(m["profit_factor"], 3)
                    if m["profit_factor"] != float("inf")
                    else 99.9,
                    "win_rate": round(m["win_rate"], 3),
                    "net_profit": round(m["net_profit"], 2),
                    "avg_pnl": round(m["avg_pnl"], 3),
                    "max_drawdown": max_drawdown(pnls),
                    "trades": m["trades"],
                    "walk_forward": wf,
                    "overfit_risk": ovr,
                    "p_value": round(p_value_vs_rest(pnls, baseline_pnls), 4),
                    "sample_size": m["trades"],
                    "confidence_pct": confidence_pct(
                        n=m["trades"],
                        p_value=p_value_vs_rest(pnls, baseline_pnls),
                        stability=stability_score(pnls),
                        walk_forward_ok=wf["passed"],
                        overfit=ovr,
                    ),
                }
            )

    current = next(
        (
            c
            for c in combos
            if abs(c["activation"] - cur_act) < 0.001 and abs(c["distance"] - cur_dist) < 0.001
        ),
        combos[0] if combos else None,
    )
    best = max(combos, key=lambda c: (c["profit_factor"], c["net_profit"]), default=None)

    recommendation: dict[str, Any] = {
        "action": "KEEP",
        "current_activation": cur_act,
        "current_distance": cur_dist,
        "recommended_activation": cur_act,
        "recommended_distance": cur_dist,
        "confidence_pct": current["confidence_pct"] if current else 0,
        "reasons": [],
    }

    if best and current and (
        best["activation"] != current["activation"] or best["distance"] != current["distance"]
    ):
        if best["profit_factor"] > current["profit_factor"]:
            recommendation.update(
                {
                    "action": "CHANGE",
                    "recommended_activation": best["activation"],
                    "recommended_distance": best["distance"],
                    "confidence_pct": best["confidence_pct"],
                    "candidate": best,
                    "reasons": [
                        f"PF {best['profit_factor']:.2f} vs {current['profit_factor']:.2f}",
                        f"activation {best['activation']:.3f} / distance {best['distance']:.3f}",
                    ],
                }
            )

    return {
        "current": {"activation": cur_act, "distance": cur_dist},
        "combos": combos,
        "best": best,
        "recommendation": recommendation,
    }
