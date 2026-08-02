"""Monthly rule stability → PAPER_READY vs RESEARCH_ONLY."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)

Predicate = Callable[[dict[str, Any]], bool]

MIN_MONTHLY_N = 40
MIN_MONTHLY_PF = 1.1
MIN_MONTHLY_EV = 0.0
MIN_STABLE_MONTHS = 3


def _month_key(row: dict[str, Any]) -> str | None:
    ts = row.get("closed_at") or row.get("opened_at") or row.get("created_at")
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except Exception:
        return None
    return f"{dt.year:04d}-{dt.month:02d}"


def monthly_stability(
    rows: list[dict[str, Any]],
    pred: Predicate,
    *,
    min_n: int = MIN_MONTHLY_N,
    min_pf: float = MIN_MONTHLY_PF,
    min_ev: float = MIN_MONTHLY_EV,
    min_stable: int = MIN_STABLE_MONTHS,
) -> dict[str, Any]:
    """Build Month/WR/PF/EV/Trades; mark PAPER_READY if ≥3 consecutive stable months."""
    by_month: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if not pred(r):
            continue
        mk = _month_key(r)
        if mk is None:
            continue
        try:
            by_month[mk].append(float(r["pnl"]))
        except Exception:
            continue

    months = sorted(by_month.keys())
    series: list[dict[str, Any]] = []
    for mk in months:
        met = trade_metrics(by_month[mk])
        pf = met.get("pf")
        pf_v = 99.0 if (pf is None and met.get("pf_inf")) else float(pf or 0.0)
        stable = (
            int(met.get("n") or 0) >= min_n
            and float(met.get("ev") or 0) >= min_ev
            and pf_v >= min_pf
        )
        series.append({
            "month": mk,
            "n": met["n"],
            "wr": met["wr"],
            "pf": met["pf"],
            "pf_inf": met.get("pf_inf"),
            "ev": met["ev"],
            "stable": stable,
        })

    # Longest trailing consecutive stable streak ending at latest month
    streak = 0
    for row in reversed(series):
        if row["stable"]:
            streak += 1
        else:
            break

    # Also accept any 3+ consecutive anywhere if last month is still in a streak ≥3
    best = 0
    cur = 0
    for row in series:
        if row["stable"]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0

    paper_ready = streak >= min_stable
    return {
        "months": series,
        "stable_streak": streak,
        "best_stable_streak": best,
        "status": "PAPER_READY" if paper_ready else "RESEARCH_ONLY",
        "paper_ready": paper_ready,
    }


def attach_stability(
    rule: dict[str, Any],
    rows: list[dict[str, Any]],
    pred: Predicate | None,
) -> dict[str, Any]:
    out = dict(rule)
    if pred is None:
        out["stability_status"] = "RESEARCH_ONLY"
        out["stability"] = {"status": "RESEARCH_ONLY", "reason": "no_predicate"}
        return out
    stab = monthly_stability(rows, pred)
    out["stability"] = stab
    out["stability_status"] = stab["status"]
    return out


__all__ = [
    "MIN_STABLE_MONTHS",
    "attach_stability",
    "monthly_stability",
]
