"""Section 38 — market/trade distribution regime change."""

from __future__ import annotations

import statistics
from typing import Any

from bot.report.analytics import trade_pnl


def build_regime_change_detector(closed: list[Any]) -> dict[str, Any]:
    if len(closed) < 400:
        return {
            "changed": False,
            "level": "LOW",
            "message": "Need at least 400 trades for regime change detection",
        }

    recent = closed[-300:]
    baseline = closed[-1300:-300] if len(closed) >= 1300 else closed[:-300]

    def _means(trades: list[Any]) -> dict[str, float]:
        if not trades:
            return {}
        entries = [float(t["entry_price"]) for t in trades]
        holds = [float(t["holding_time_seconds"] or 0) for t in trades]
        pnls = [trade_pnl(t) for t in trades]
        return {
            "avg_entry": statistics.mean(entries),
            "avg_hold": statistics.mean(holds),
            "avg_pnl": statistics.mean(pnls),
        }

    base_m = _means(baseline)
    rec_m = _means(recent)
    if not base_m or not rec_m:
        return {"changed": False, "level": "LOW", "message": "Insufficient trade data"}

    shifts = []
    for key in ("avg_entry", "avg_hold", "avg_pnl"):
        base = base_m[key]
        rec = rec_m[key]
        if base != 0:
            pct = abs(rec - base) / abs(base) * 100
            shifts.append(pct)

    max_shift = max(shifts) if shifts else 0
    changed = max_shift > 25 or abs(rec_m["avg_pnl"] - base_m["avg_pnl"]) > 2.0
    level = "HIGH" if max_shift > 40 else "MEDIUM" if changed else "LOW"

    return {
        "changed": changed,
        "level": level,
        "recent_n": len(recent),
        "baseline_n": len(baseline),
        "max_shift_pct": round(max_shift, 1),
        "recent_means": {k: round(v, 3) for k, v in rec_m.items()},
        "baseline_means": {k: round(v, 3) for k, v in base_m.items()},
        "message": (
            f"Last {len(recent)} trades differ from prior {len(baseline)} "
            f"(max shift {max_shift:.0f}%)"
            if changed
            else "Recent trade distribution similar to baseline"
        ),
    }
