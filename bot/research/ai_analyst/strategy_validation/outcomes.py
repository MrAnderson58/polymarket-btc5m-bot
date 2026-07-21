"""S48 — signal outcome aggregation after paper trade close."""

from __future__ import annotations

from typing import Any

from bot.research.ai_analyst.paper_trading.models import (
    EXIT_STOP,
    EXIT_TP1,
    EXIT_TP2,
    EXIT_TP3,
    PaperTrade,
)
from bot.research.ai_analyst.strategy_validation.history import SignalHistoryRecord


def tp_level_reached(trade: PaperTrade) -> str:
    if trade.tp3_hit:
        return EXIT_TP3
    if trade.tp2_hit:
        return EXIT_TP2
    if trade.tp1_hit:
        return EXIT_TP1
    if trade.exit_reason == EXIT_STOP:
        return EXIT_STOP
    return str(trade.exit_reason or "NONE")


def result_label(pnl_usd: float) -> str:
    if pnl_usd > 0:
        return "WIN"
    if pnl_usd < 0:
        return "LOSS"
    return "BE"


def outcome_from_trade(
    trade: PaperTrade,
    *,
    history: SignalHistoryRecord | None = None,
) -> dict[str, Any]:
    """Build outcome row from a closed PaperTrade (+ optional history)."""
    reasons = list(trade.reasons)
    tags: list[str] = []
    score = None
    confidence = trade.confidence
    if history is not None:
        reasons = list(history.reasons) or reasons
        tags = list(history.tags)
        score = history.score
        confidence = history.confidence

    return {
        "signal_id": trade.signal_id,
        "trade_id": trade.trade_id,
        "closed_at": int(trade.closed_at or 0),
        "result": result_label(float(trade.pnl_usd)),
        "r_multiple": float(trade.r_multiple),
        "pnl_usd": float(trade.pnl_usd),
        "mfe_pct": float(trade.mfe_pct),
        "mae_pct": float(trade.mae_pct),
        "hold_time_sec": int(trade.holding_seconds),
        "tp_level_reached": tp_level_reached(trade),
        "exit_reason": trade.exit_reason,
        "strategy": trade.strategy,
        "market": trade.symbol,
        "direction": trade.direction,
        "confidence": confidence,
        "score": score,
        "reasons": reasons,
        "tags": tags,
    }


def aggregate_exit_breakdown(outcomes: list[dict[str, Any]]) -> dict[str, float]:
    n = len(outcomes) or 1
    counts = {EXIT_TP1: 0, EXIT_TP2: 0, EXIT_TP3: 0, EXIT_STOP: 0, "OTHER": 0}
    for o in outcomes:
        level = str(o.get("tp_level_reached") or o.get("exit_reason") or "OTHER")
        if level in counts:
            counts[level] += 1
        else:
            counts["OTHER"] += 1
    return {k: round(100.0 * v / n, 2) for k, v in counts.items()}
