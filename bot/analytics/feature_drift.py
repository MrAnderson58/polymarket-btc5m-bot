"""Section 37 — feature drift vs historical baseline."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.report.analytics import ENTRY_PRICES, _metrics, trade_pnl


def build_feature_drift(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
    report: dict[str, Any],
) -> dict[str, Any]:
    del conn
    if len(closed) < 40:
        return {"alerts": [], "status": "insufficient_data"}

    split = len(closed) // 2
    baseline = closed[:split]
    recent = closed[split:]

    alerts: list[dict[str, Any]] = []

    for price in ENTRY_PRICES:
        base_pnls = [
            trade_pnl(t)
            for t in baseline
            if abs(float(t["entry_price"]) - price) <= 0.005
        ]
        recent_pnls = [
            trade_pnl(t)
            for t in recent
            if abs(float(t["entry_price"]) - price) <= 0.005
        ]
        if len(base_pnls) < 8 or len(recent_pnls) < 5:
            continue
        base_pf = _metrics(base_pnls)["profit_factor"]
        recent_pf = _metrics(recent_pnls)["profit_factor"]
        if base_pf == float("inf") or recent_pf == float("inf"):
            continue
        if base_pf >= 1.5 and recent_pf < base_pf * 0.6:
            alerts.append(
                {
                    "feature": "entry_threshold",
                    "value": price,
                    "baseline_pf": round(base_pf, 2),
                    "recent_pf": round(recent_pf, 2),
                    "message": f"Entry {price:.2f} degraded (PF {base_pf:.1f} → {recent_pf:.1f})",
                }
            )

    mem = report.get("ai_memory", {})
    trends = mem.get("trends", {})
    t30 = trends.get("30", {})
    if t30.get("direction") == "down" and t30.get("pf_delta", 0) < -0.3:
        alerts.append(
            {
                "feature": "overall_strategy",
                "message": f"30-report PF trend down Δ{t30['pf_delta']:+.2f}",
                "baseline_pf": None,
                "recent_pf": None,
            }
        )

    drift = report.get("drift_detector", {})
    if drift.get("status") == "degrading":
        alerts.append(
            {
                "feature": "recent_performance",
                "message": drift.get("message", "Recent trades underperform baseline"),
                "baseline_pf": drift.get("baseline_avg_pnl"),
                "recent_pf": drift.get("recent_avg_pnl"),
            }
        )

    return {
        "status": "drift_detected" if alerts else "stable",
        "alerts": alerts,
    }
