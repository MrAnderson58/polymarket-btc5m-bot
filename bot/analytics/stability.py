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
    if wr_values:
        score += max(0.0, 15.0 - _variance_penalty(wr_values) * 0.5)
    if avg_values:
        score += max(0.0, 15.0 - _variance_penalty(avg_values) * 0.5)
    score += 10.0 if walk_forward_ok else 0.0
    score += max(0.0, 10.0 - monte_carlo_ruin * 100)
    return max(0.0, min(100.0, score))


def _wf_profit_factor(row: dict[str, Any]) -> float | None:
    for key in ("profit_factor", "test_pf", "train_pf"):
        value = row.get(key)
        if value is None or value == float("inf"):
            continue
        return float(value)
    return None


def _wf_win_rate(row: dict[str, Any]) -> float | None:
    value = row.get("win_rate")
    if value is None:
        return None
    return float(value)


def _wf_avg_pnl(row: dict[str, Any]) -> float | None:
    for key in ("avg_pnl", "test_avg_pnl", "train_avg_pnl"):
        value = row.get(key)
        if value is not None:
            return float(value)
    return None


def _walk_forward_ok(walk: dict[str, Any], wf_rows: list[dict[str, Any]]) -> bool:
    if walk.get("trend") == "degrading":
        return False
    if wf_rows and "generalizes" in wf_rows[0]:
        return all(row.get("generalizes", False) for row in wf_rows)
    return walk.get("trend") != "degrading"


def build_parameter_stability(report: dict[str, Any]) -> dict[str, Any]:
    optimizer = report.get("parameter_optimizer", {})
    walk = report.get("walk_forward", {})
    monte = report.get("monte_carlo", {})
    equity = report.get("equity_curve", {})
    ruin = float(monte.get("probability_of_ruin", 0) or 0)

    wf_rows = walk.get("rows", [])
    if not isinstance(wf_rows, list):
        wf_rows = []

    pf_vals = [v for row in wf_rows if (v := _wf_profit_factor(row)) is not None]
    wr_vals = [v for row in wf_rows if (v := _wf_win_rate(row)) is not None]
    avg_vals = [v for row in wf_rows if (v := _wf_avg_pnl(row)) is not None]
    walk_ok = _walk_forward_ok(walk, wf_rows)

    rolling_pf = [v for v in equity.get("rolling_pf", []) if v is not None and v != float("inf")]
    if rolling_pf:
        pf_vals = pf_vals + rolling_pf[-5:]

    parameters: list[dict[str, Any]] = []

    cur = optimizer.get("current", {})
    opt = optimizer.get("optimal", {})
    entry_rows = optimizer.get("entry_significance", [])

    for row in entry_rows:
        trades = int(row.get("trades", 0) or 0)
        if trades < 5:
            continue
        win_rate = row.get("win_rate")
        profit_factor = row.get("profit_factor")
        avg_pnl = row.get("avg_pnl")
        entry_price = row.get("entry_price")
        if win_rate is None or profit_factor is None or avg_pnl is None or entry_price is None:
            continue
        stability = _compute_stability(
            trades=trades,
            pf_values=[float(profit_factor)],
            wr_values=[float(win_rate)],
            avg_values=[float(avg_pnl)],
            walk_forward_ok=walk_ok,
            monte_carlo_ruin=ruin,
        )
        parameters.append(
            {
                "parameter": "entry",
                "value": entry_price,
                "trades": trades,
                "profit_factor": profit_factor,
                "win_rate": win_rate,
                "stability_pct": round(stability, 1),
                "stability_label": _stability_label(stability),
                "confidence": _confidence_from_n(trades),
                "is_current": abs(float(entry_price) - float(cur.get("entry", 0))) < 0.006,
                "is_optimal": abs(float(entry_price) - float(opt.get("entry", 0))) < 0.006,
            }
        )

    for label, key, trades_key in (
        ("stop_loss", "stop_pct", "trades"),
        ("trailing_activation", "trailing_activation", "trades"),
    ):
        m = cur if key == "stop_pct" else cur
        n = int(m.get(trades_key, m.get("trades", 0)) or 0)
        stability = _compute_stability(
            trades=n,
            pf_values=pf_vals or [float(cur.get("profit_factor", 0) or 0)],
            wr_values=wr_vals or ([float(cur.get("win_rate", 0) or 0)] if cur.get("win_rate") is not None else []),
            avg_values=avg_vals or [float(cur.get("avg_pnl", 0) or 0)],
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
