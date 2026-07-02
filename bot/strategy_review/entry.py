"""Entry threshold analysis (0.34–0.40)."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.config import effective_entry_threshold
from bot.report.analytics import ENTRY_PRICES, fetch_v2_trades, trade_pnl
from bot.strategy_review.constants import ENTRY_LEVELS
from bot.strategy_review.metrics import enrich_metrics


def analyze_entry(
    conn: sqlite3.Connection,
    *,
    closed: list | None = None,
    current_entry: float | None = None,
) -> dict[str, Any]:
    closed = closed if closed is not None else fetch_v2_trades(conn, closed_only=True)
    current = current_entry if current_entry is not None else effective_entry_threshold(0.40)
    current = round(current, 2)

    all_pnls = [trade_pnl(t) for t in closed]
    rows: list[dict[str, Any]] = []

    for price in ENTRY_LEVELS:
        subset = [
            t for t in closed if abs(float(t["entry_price"]) - price) <= 0.005
        ]
        pnls = [trade_pnl(t) for t in subset]
        rest = [
            trade_pnl(t)
            for t in closed
            if abs(float(t["entry_price"]) - price) > 0.005
        ]
        row = enrich_metrics(pnls, rest=rest)
        row["entry_price"] = price
        rows.append(row)

    current_row = next((r for r in rows if r["entry_price"] == current), None)
    if current_row is None:
        nearest = min(ENTRY_PRICES, key=lambda p: abs(p - current))
        current_row = next((r for r in rows if r["entry_price"] == nearest), rows[0])

    candidates = [
        r
        for r in rows
        if r["trades"] >= 10
        and r["entry_price"] != current_row["entry_price"]
        and r["profit_factor"] > current_row["profit_factor"]
        and r["avg_pnl"] > current_row["avg_pnl"]
    ]
    best = max(candidates, key=lambda r: (r["profit_factor"], r["avg_pnl"]), default=None)

    recommendation = {
        "action": "KEEP",
        "current": current_row["entry_price"],
        "recommended": current_row["entry_price"],
        "confidence_pct": current_row["confidence_pct"],
        "reasons": [],
    }

    if best:
        recommendation.update(
            {
                "action": "CHANGE",
                "recommended": best["entry_price"],
                "confidence_pct": best["confidence_pct"],
                "candidate": best,
                "reasons": _reason_lines(best, current_row),
            }
        )
    elif current_row["sample_size"] < 30:
        recommendation["reasons"] = ["Insufficient evidence"]

    return {
        "current_entry": current_row["entry_price"],
        "rows": rows,
        "recommendation": recommendation,
    }


def _reason_lines(best: dict[str, Any], current: dict[str, Any]) -> list[str]:
    lines = []
    if best["profit_factor"] > current["profit_factor"]:
        lines.append("PF выше")
    if best["sample_size"] >= 300:
        lines.append(f"выборка {best['sample_size']} сделок")
    elif best["sample_size"] >= 30:
        lines.append(f"выборка {best['sample_size']} сделок (ниже целевых 300)")
    if best["walk_forward"].get("passed"):
        lines.append("walk-forward PASS")
    else:
        lines.append("walk-forward FAIL")
    lines.append(f"stability {best['stability_score']:.0f}%")
    lines.append(f"overfit {best['overfit_risk']}")
    return lines
