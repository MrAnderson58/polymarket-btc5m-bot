"""Book performance metrics + overlap (research-only)."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def book_stats_from_pnls(pnls: Sequence[float]) -> dict[str, Any]:
    met = trade_metrics(list(pnls))
    extra = cluster_performance(list(pnls)) if pnls else {}
    return {
        "trades": int(met.get("n") or 0),
        "wr": met.get("wr"),
        "pf": met.get("pf"),
        "pf_inf": met.get("pf_inf"),
        "ev": met.get("ev"),
        "sharpe": extra.get("sharpe"),
        "max_dd": extra.get("max_dd"),
        "total": met.get("total"),
    }


def book_stats_from_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    accepted = [r for r in rows if int(r.get("accepted") or 0) == 1 and r.get("pnl") is not None]
    pnls = []
    for r in accepted:
        try:
            pnls.append(float(r["pnl"]))
        except Exception:
            continue
    return book_stats_from_pnls(pnls)


def trade_id_set(rows: Sequence[dict[str, Any]], *, accepted_only: bool = True) -> set[int]:
    out: set[int] = set()
    for r in rows:
        if accepted_only and int(r.get("accepted") or 0) != 1:
            continue
        try:
            out.add(int(r.get("trade_id") or 0))
        except Exception:
            continue
    return {x for x in out if x}


def book_overlap(
    rows_a: Sequence[dict[str, Any]],
    rows_b: Sequence[dict[str, Any]],
    rows_c: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    a = trade_id_set(rows_a)
    b = trade_id_set(rows_b)
    c = trade_id_set(rows_c)
    return {
        "a_and_b": len(a & b),
        "a_and_c": len(a & c),
        "b_and_c": len(b & c),
        "a_only": len(a - b - c),
        "b_only": len(b - a - c),
        "c_only": len(c - a - b),
        "filtered_b": len(a - b),
        "filtered_c": len(a - c),
        "n_a": len(a),
        "n_b": len(b),
        "n_c": len(c),
    }


def improvement(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    def _delta(key: str) -> float | None:
        bv = base.get(key)
        ov = other.get(key)
        if bv is None or ov is None:
            return None
        try:
            return round(float(ov) - float(bv), 4)
        except Exception:
            return None

    return {
        "wr_delta": _delta("wr"),
        "pf_delta": _delta("pf"),
        "ev_delta": _delta("ev"),
        "sharpe_delta": _delta("sharpe"),
        "trades_delta": (other.get("trades") or 0) - (base.get("trades") or 0),
    }


def partition_by_book(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out = {BOOK_A: [], BOOK_B: [], BOOK_C: []}
    for r in rows:
        b = str(r.get("book") or "")
        if b in out:
            out[b].append(r)
    return out


__all__ = [
    "book_overlap",
    "book_stats_from_pnls",
    "book_stats_from_rows",
    "improvement",
    "partition_by_book",
    "trade_id_set",
]
