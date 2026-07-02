"""Section 31 — per-trade execution audit."""

from __future__ import annotations

from typing import Any

from bot.analytics.intelligence_context import IntelligenceContext
from bot.config import ER_V2_STOP_LOSS_PCT
from bot.er_btc_direction_stats import _exit_ts
from bot.report.analytics import _pnl_pct, trade_pnl


def _best_alt_exit(trade: Any, ctx: IntelligenceContext) -> tuple[float | None, float | None]:
    entry = float(trade["entry_price"])
    series = ctx.cache.bid_series(
        market_slug=str(trade["market_slug"]),
        side=str(trade["side"]),
        start_ts=int(trade["entry_ts"]),
        end_ts=_exit_ts(trade),
    )
    if not series:
        return None, None
    best_bid = max(b for _, b in series)
    return best_bid, _pnl_pct(entry, best_bid)


def _liquidity_ok(trade: Any, ctx: IntelligenceContext) -> bool:
    bid, ask, _dist, _sec = ctx.cache.quote_at_entry(
        market_slug=str(trade["market_slug"]),
        side=str(trade["side"]),
        entry_ts=int(trade["entry_ts"]),
    )
    if bid is None or ask is None:
        feat = ctx.feature_by_trade_id.get(int(trade["id"]))
        if feat and feat.get("spread") is not None:
            return float(feat["spread"]) <= 0.025
        return False
    return float(ask) - float(bid) <= 0.025


def build_execution_audit(
    closed: list[Any],
    *,
    ctx: IntelligenceContext,
) -> dict[str, Any]:
    stop_pct = ER_V2_STOP_LOSS_PCT
    trades_out: list[dict[str, Any]] = []
    execution_loss = 0.0
    strategy_loss = 0.0
    exchange_loss = 0.0

    for trade in closed[-100:]:
        entry = float(trade["entry_price"])
        exit_price = float(trade["exit_price"] or entry)
        actual_pnl = trade_pnl(trade)
        expected_stop_price = entry * (1 + stop_pct / 100)
        expected_stop_pnl = _pnl_pct(entry, expected_stop_price)
        reason = trade["exit_reason"] or ""

        stop_should = reason == "STOP_LOSS" or (
            reason != "TRAILING_STOP" and exit_price <= expected_stop_price + 1e-6
        )
        slug = str(trade["market_slug"])
        slippage = ctx.slippage_by_slug.get(slug)
        best_bid, best_pnl = _best_alt_exit(trade, ctx)

        exec_component = 0.0
        if reason == "STOP_LOSS" and actual_pnl < expected_stop_pnl - 0.5:
            exec_component = actual_pnl - expected_stop_pnl
            exchange_loss += abs(exec_component)
        elif slippage is not None and slippage < 0:
            exec_component = slippage
            exchange_loss += abs(slippage)

        strat_component = actual_pnl
        if best_pnl is not None and best_pnl > actual_pnl:
            strat_component = best_pnl - actual_pnl
            strategy_loss += max(0.0, best_pnl - actual_pnl)

        execution_loss += abs(exec_component)

        trades_out.append(
            {
                "trade_id": int(trade["id"]),
                "entry": entry,
                "exit": exit_price,
                "exit_reason": reason,
                "stop_should_trigger": stop_should,
                "expected_stop_pnl_pct": round(expected_stop_pnl, 2),
                "actual_pnl_pct": round(actual_pnl, 2),
                "slippage_pct": round(slippage, 3) if slippage is not None else None,
                "execution_loss_pct": round(exec_component, 2),
                "best_possible_pnl_pct": round(best_pnl, 2) if best_pnl is not None else None,
                "liquidity_ok": _liquidity_ok(trade, ctx),
            }
        )

    return {
        "trades": trades_out,
        "summary": {
            "execution_loss_pct": round(execution_loss, 2),
            "strategy_loss_pct": round(strategy_loss, 2),
            "exchange_loss_pct": round(exchange_loss, 2),
            "audited_trades": len(trades_out),
        },
    }
