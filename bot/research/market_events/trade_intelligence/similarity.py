"""Similarity helpers for Trade Intelligence V1 (deterministic, no LLM)."""

from __future__ import annotations

from bot.research.market_events.trade_intelligence.models import TradeRecord


def _num_sim(a: float | None, b: float | None, *, scale: float) -> float:
    if a is None or b is None or scale <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(float(a) - float(b)) / scale)


def trade_similarity(left: TradeRecord, right: TradeRecord) -> float:
    """Score in [0, 1] — symbol/side/strategy + coarse pnl/duration similarity."""
    score = 0.0
    weight = 0.0

    def add(w: float, v: float) -> None:
        nonlocal score, weight
        score += w * v
        weight += w

    add(0.30, 1.0 if left.symbol.upper() == right.symbol.upper() else 0.0)
    add(0.20, 1.0 if left.side.upper() == right.side.upper() else 0.0)
    if left.strategy or right.strategy:
        same = (left.strategy or "").upper() == (right.strategy or "").upper()
        add(0.15, 1.0 if same else 0.0)
    add(0.20, _num_sim(left.pnl_pct, right.pnl_pct, scale=5.0))
    add(0.15, _num_sim(left.pnl_usd, right.pnl_usd, scale=50.0))

    dur_l = None
    dur_r = None
    if left.entry_ts is not None and left.exit_ts is not None:
        dur_l = float(max(0, left.exit_ts - left.entry_ts))
    if right.entry_ts is not None and right.exit_ts is not None:
        dur_r = float(max(0, right.exit_ts - right.entry_ts))
    add(0.10, _num_sim(dur_l, dur_r, scale=3600.0))

    return score / weight if weight else 0.0


def find_similar_trades(
    target: TradeRecord,
    candidates: list[TradeRecord],
    *,
    limit: int = 10,
    min_score: float = 0.25,
) -> list[tuple[TradeRecord, float]]:
    scored: list[tuple[TradeRecord, float]] = []
    for c in candidates:
        if target.id is not None and c.id == target.id:
            continue
        s = trade_similarity(target, c)
        if s >= min_score:
            scored.append((c, s))
    scored.sort(key=lambda x: (-x[1], -(x[0].id or 0)))
    return scored[: max(1, int(limit))]
