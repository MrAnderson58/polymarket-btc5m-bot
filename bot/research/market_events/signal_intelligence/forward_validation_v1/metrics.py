"""Metrics + alerts for Forward Validation Monitor V1 (observe only)."""

from __future__ import annotations

import math
from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def closed_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if str(r.get("status") or "") == "closed"]
    pnls: list[float] = []
    holds: list[float] = []
    maes: list[float] = []
    correct = 0
    judged = 0
    exp_wrs: list[float] = []
    for r in closed:
        if r.get("pnl") is not None:
            try:
                pnls.append(float(r["pnl"]))
            except Exception:
                pass
        if r.get("holding_time_sec") is not None:
            try:
                holds.append(float(r["holding_time_sec"]))
            except Exception:
                pass
        if r.get("mae") is not None:
            try:
                maes.append(abs(float(r["mae"])))
            except Exception:
                pass
        if r.get("prediction_correct") is not None:
            judged += 1
            if int(r["prediction_correct"]) == 1:
                correct += 1
        ewr = r.get("expected_wr") if r.get("expected_wr") is not None else r.get("historical_wr")
        if ewr is not None:
            try:
                x = float(ewr)
                exp_wrs.append(x * 100.0 if x <= 1.5 else x)
            except Exception:
                pass

    met = trade_metrics(pnls)
    # Sharpe on closed pnls
    sharpe = None
    if len(pnls) >= 5:
        mu = sum(pnls) / len(pnls)
        var = sum((p - mu) ** 2 for p in pnls) / max(len(pnls) - 1, 1)
        sd = math.sqrt(var) if var > 0 else 0.0
        if sd > 1e-12:
            sharpe = round(mu / sd * math.sqrt(len(pnls)), 4)

    actual_wr = met.get("wr")
    expected_wr = round(sum(exp_wrs) / len(exp_wrs), 4) if exp_wrs else None
    expected_ev = None
    evs = []
    for r in closed:
        if r.get("historical_ev") is not None:
            try:
                evs.append(float(r["historical_ev"]))
            except Exception:
                pass
    if evs:
        expected_ev = round(sum(evs) / len(evs), 6)

    return {
        "n_closed": len(closed),
        "n_open": sum(1 for r in rows if str(r.get("status") or "") == "open"),
        "n_total": len(rows),
        "wr": actual_wr,
        "pf": met.get("pf"),
        "ev": met.get("ev"),
        "sharpe": sharpe,
        "avg_hold_sec": round(sum(holds) / len(holds), 2) if holds else None,
        "avg_dd": round(sum(maes) / len(maes), 6) if maes else None,
        "prediction_accuracy": round(100.0 * correct / judged, 2) if judged else None,
        "expected_wr": expected_wr,
        "actual_wr": actual_wr,
        "expected_ev": expected_ev,
        "actual_ev": met.get("ev"),
        "total_pnl": met.get("total"),
    }


def detect_alerts(
    *,
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    book_metrics: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Observe-only alerts — no strategy action."""
    alerts: list[dict[str, Any]] = []
    prev = previous or {}

    def _drop(name: str, cur_v: Any, prev_v: Any, *, thresh: float) -> None:
        try:
            c = float(cur_v)
            p = float(prev_v)
        except Exception:
            return
        if p - c >= thresh:
            alerts.append({
                "type": f"{name}_drop",
                "previous": p,
                "current": c,
                "delta": round(c - p, 6),
            })

    _drop("reality", current.get("reality_score"), prev.get("reality_score"), thresh=5.0)
    _drop("wr", current.get("wr"), prev.get("wr"), thresh=5.0)
    # PF drop: relative
    try:
        cpf = float(current.get("pf"))
        ppf = float(prev.get("pf"))
        if ppf > 0 and (ppf - cpf) / ppf >= 0.15:
            alerts.append({
                "type": "pf_drop",
                "previous": ppf,
                "current": cpf,
                "delta": round(cpf - ppf, 6),
            })
    except Exception:
        pass

    # Book D vs others (new trades only metrics already in book_metrics)
    d = book_metrics.get("book_d") or {}
    b = book_metrics.get("book_b") or {}
    try:
        d_ev = float(d.get("ev") or 0)
        b_ev = float(b.get("ev") or 0)
        if d.get("n_closed", 0) >= 3 and b.get("n_closed", 0) >= 3:
            if d_ev < b_ev - 1e-9:
                alerts.append({
                    "type": "book_d_underperforms",
                    "book_d_ev": d_ev,
                    "book_b_ev": b_ev,
                })
            elif d_ev > b_ev + 1e-9:
                alerts.append({
                    "type": "book_d_improves",
                    "book_d_ev": d_ev,
                    "book_b_ev": b_ev,
                })
    except Exception:
        pass

    # Prediction drift: accuracy drop
    _drop(
        "prediction",
        current.get("prediction_accuracy"),
        prev.get("prediction_accuracy"),
        thresh=10.0,
    )
    # rename type
    for a in alerts:
        if a["type"] == "prediction_drop":
            a["type"] = "prediction_drift"

    return alerts


__all__ = ["closed_metrics", "detect_alerts"]
