"""Parameter stability scoring (Report v3 §21)."""

from __future__ import annotations

import statistics
from typing import Any


def _stability_label(score: float) -> str:
    if score < 40:
        return "unstable"
    if score < 70:
        return "medium"
    return "stable"


def _confidence_from_n(n: int) -> str:
    if n >= 100:
        return "HIGH"
    if n >= 30:
        return "MEDIUM"
    return "LOW"


def _variance_penalty(values: list[float]) -> float:
    if len(values) < 2:
        return 30.0
    mean = statistics.mean(values)
    if mean == 0:
        return 20.0
    cv = statistics.pstdev(values) / abs(mean)
    return min(40.0, cv * 100)


def _compute_stability(
    *,
    trades: int,
    pf_values: list[float],
    wr_values: list[float],
    avg_values: list[float],
    walk_forward_ok: bool,
    monte_carlo_ruin: float,
) -> float:
    score = 0.0
    score += min(30.0, trades / 10.0)
    score += max(0.0, 20.0 - _variance_penalty(pf_values))
    score += max(0.0, 15.0 - _variance_penalty(wr_values) * 0.5)
    score += max(0.0, 15.0 - _variance_penalty(avg_values) * 0.5)
    score += 10.0 if walk_forward_ok else 0.0
    score += max(0.0, 10.0 - monte_carlo_ruin * 100)
    return max(0.0, min(100.0, score))


def build_parameter_stability(report: dict[str, Any]) -> dict[str, Any]:
    optimizer = report.get("parameter_optimizer", {})
    walk = report.get("walk_forward", {})
    monte = report.get("monte_carlo", {})
    equity = report.get("equity_curve", {})
    ruin = float(monte.get("probability_of_ruin", 0) or 0)

    wf_rows = walk.get("rows", [])
    pf_vals = [r["profit_factor"] for r in wf_rows if r.get("profit_factor") not in (None, float("inf"))]
    wr_vals = [r["win_rate"] for r in wf_rows]
    avg_vals = [r["avg_pnl"] for r in wf_rows]
    walk_ok = walk.get("trend") != "degrading"

    rolling_pf = [v for v in equity.get("rolling_pf", []) if v is not None and v != float("inf")]
    if rolling_pf:
        pf_vals = pf_vals + rolling_pf[-5:]

    parameters: list[dict[str, Any]] = []

    cur = optimizer.get("current", {})
    opt = optimizer.get("optimal", {})
    entry_rows = optimizer.get("entry_significance", [])

    for row in entry_rows:
        if row.get("trades", 0) < 5:
            continue
        stability = _compute_stability(
            trades=row["trades"],
            pf_values=[row["profit_factor"]],
            wr_values=[row["win_rate"]],
            avg_values=[row["avg_pnl"]],
            walk_forward_ok=walk_ok,
            monte_carlo_ruin=ruin,
        )
        parameters.append(
            {
                "parameter": "entry",
                "value": row["entry_price"],
                "trades": row["trades"],
                "profit_factor": row["profit_factor"],
                "win_rate": row["win_rate"],
                "stability_pct": round(stability, 1),
                "stability_label": _stability_label(stability),
                "confidence": _confidence_from_n(row["trades"]),
                "is_current": abs(row["entry_price"] - cur.get("entry", 0)) < 0.006,
                "is_optimal": abs(row["entry_price"] - opt.get("entry", 0)) < 0.006,
            }
        )

    for label, key, trades_key in (
        ("stop_loss", "stop_pct", "trades"),
        ("trailing_activation", "trailing_activation", "trades"),
    ):
        m = cur if key == "stop_pct" else cur
        n = int(m.get(trades_key, m.get("trades", 0)))
        stability = _compute_stability(
            trades=n,
            pf_values=pf_vals or [cur.get("profit_factor", 0)],
            wr_values=wr_vals or [cur.get("win_rate", 0)],
            avg_values=avg_vals or [cur.get("avg_pnl", 0)],
            walk_forward_ok=walk_ok,
            monte_carlo_ruin=ruin,
        )
        val = cur.get(key)
        if val is None:
            continue
        parameters.append(
            {
                "parameter": label,
                "value": abs(val) if label == "stop_loss" else val,
                "trades": n,
                "profit_factor": cur.get("profit_factor", 0),
                "win_rate": cur.get("win_rate", 0),
                "stability_pct": round(stability, 1),
                "stability_label": _stability_label(stability),
                "confidence": _confidence_from_n(n),
                "is_current": True,
                "is_optimal": abs(val - opt.get(key, val)) < 1e-6,
            }
        )

    return {"parameters": parameters}
