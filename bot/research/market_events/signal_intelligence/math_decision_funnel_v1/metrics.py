"""Metrics helpers for Math Decision Funnel V1."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_stats_from_pnls,
)


def pnl_list(rows: Sequence[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get("pnl")
        if v is None:
            v = r.get("actual_pnl")
        if v is None:
            continue
        try:
            out.append(float(v))
        except Exception:
            continue
    return out


def stage_metrics(rows: Sequence[dict[str, Any]], *, input_n: int | None = None) -> dict[str, Any]:
    n_in = int(input_n if input_n is not None else len(rows))
    n_acc = len(rows)
    n_rej = max(0, n_in - n_acc)
    stats = book_stats_from_pnls(pnl_list(rows))
    pct = round(100.0 * n_acc / n_in, 4) if n_in else 0.0
    return {
        "input_n": n_in,
        "accepted_n": n_acc,
        "rejected_n": n_rej,
        "acceptance_pct": pct,
        "wr": stats.get("wr"),
        "pf": stats.get("pf"),
        "ev": stats.get("ev"),
        "sharpe": stats.get("sharpe"),
        "total": stats.get("total"),
        "trades": stats.get("trades"),
    }


def delta_metric(a: Any, b: Any) -> float | None:
    """b - a (influence of applying filter: after - before on survivors)."""
    if a is None or b is None:
        return None
    try:
        return round(float(b) - float(a), 6)
    except Exception:
        return None


__all__ = ["delta_metric", "pnl_list", "stage_metrics"]
