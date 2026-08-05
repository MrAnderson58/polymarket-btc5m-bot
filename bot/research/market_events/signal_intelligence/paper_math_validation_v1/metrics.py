"""Metrics helpers for Paper Math books."""

from __future__ import annotations

from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_stats_from_pnls,
    book_stats_from_rows,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def rows_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    accepted = [r for r in rows if int(r.get("accepted") or 0) == 1]
    pnls: list[float] = []
    for r in accepted:
        if r.get("pnl") is None:
            continue
        try:
            pnls.append(float(r["pnl"]))
        except Exception:
            continue
    stats = book_stats_from_pnls(pnls)
    met = trade_metrics(pnls)
    return {
        **stats,
        "accepted": len(accepted),
        "candidates": len(rows),
        "rejected": len(rows) - len(accepted),
        "avg_confidence": _avg([r.get("confidence") or r.get("decision_score") for r in accepted]),
        "avg_reality": _avg([r.get("reality_score") for r in accepted]),
        "expected_wr": _avg([r.get("historical_wr") for r in accepted]),
        "expected_ev": _avg([r.get("historical_ev") for r in accepted]),
        "actual_wr": met.get("wr"),
        "actual_ev": met.get("ev"),
        "pf": met.get("pf"),
        "total": met.get("total"),
    }


def _avg(xs: Sequence[Any]) -> float | None:
    vals: list[float] = []
    for x in xs:
        if x is None:
            continue
        try:
            vals.append(float(x))
        except Exception:
            continue
    if not vals:
        return None
    return round(sum(vals) / len(vals), 6)


def rejection_histogram(rows: Sequence[dict[str, Any]]) -> list[tuple[str, int]]:
    from collections import Counter
    import json

    c: Counter[str] = Counter()
    for r in rows:
        if int(r.get("accepted") or 0) == 1:
            continue
        raw = r.get("rejection_reasons")
        reasons: list[str] = []
        if isinstance(raw, list):
            reasons = [str(x) for x in raw]
        elif isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    reasons = [str(x) for x in parsed]
                else:
                    reasons = [raw]
            except Exception:
                reasons = [raw]
        if not reasons:
            reasons = ["unspecified"]
        for reason in reasons:
            c[reason] += 1
    return c.most_common(15)


__all__ = ["book_stats_from_rows", "rejection_histogram", "rows_metrics"]
