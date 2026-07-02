"""Risk limits for opening new positions."""

from __future__ import annotations

import inspect
import logging
import sqlite3
from dataclasses import dataclass

from bot.config import MAX_DAILY_LOSS_USDC, MAX_OPEN_POSITIONS, is_live_trading_enabled
from bot.database import count_all_open_positions, daily_realized_pnl_usdc

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskCheckResult:
    allowed: bool
    reason: str | None = None


def _entry_context_from_caller() -> dict | None:
    frame = inspect.currentframe()
    try:
        while frame is not None:
            order = frame.f_locals.get("order")
            if order is not None and hasattr(order, "price"):
                return {
                    "entry_price": float(order.price),
                    "strategy_name": getattr(order, "strategy_name", ""),
                    "market_slug": getattr(order, "market_slug", ""),
                    "side": getattr(order, "side", "NO"),
                    "size_usdc": float(getattr(order, "size_usdc", 0) or 0),
                }
            frame = frame.f_back
    finally:
        del frame
    return None


def check_can_open_position(conn: sqlite3.Connection) -> RiskCheckResult:
    from bot.portfolio.approval import evaluate_live_approval
    from bot.portfolio.guards import check_portfolio_guards
    from bot.portfolio.journal import record_entry_decision
    from bot.portfolio.kill_switch import is_kill_switch_active, kill_switch_reason
    from bot.portfolio.sizing import max_allowed_usdc

    if is_live_trading_enabled() and is_kill_switch_active():
        reason = f"KILL SWITCH — {kill_switch_reason()}"
        return RiskCheckResult(allowed=False, reason=reason)

    open_count = count_all_open_positions(conn)
    if open_count >= MAX_OPEN_POSITIONS:
        return RiskCheckResult(
            allowed=False,
            reason=(
                f"MAX_OPEN_POSITIONS reached ({open_count}/{MAX_OPEN_POSITIONS})"
            ),
        )

    daily_pnl = daily_realized_pnl_usdc(conn)
    if daily_pnl <= -MAX_DAILY_LOSS_USDC:
        return RiskCheckResult(
            allowed=False,
            reason=(
                f"MAX_DAILY_LOSS_USDC reached "
                f"(daily_pnl={daily_pnl:+.4f}, limit={MAX_DAILY_LOSS_USDC:.4f})"
            ),
        )

    guard = check_portfolio_guards(conn)
    if not guard.allowed:
        return RiskCheckResult(allowed=False, reason=guard.reason)

    ctx = _entry_context_from_caller()
    if is_live_trading_enabled() and ctx:
        size = float(ctx.get("size_usdc") or 0)
        if size > max_allowed_usdc() + 1e-6:
            return RiskCheckResult(
                allowed=False,
                reason=(
                    f"Position size ${size:.2f} exceeds live cap ${max_allowed_usdc():.2f}"
                ),
            )

    approval = evaluate_live_approval(conn, entry_context=ctx)
    if is_live_trading_enabled() and approval.has_block:
        reason = f"LIVE APPROVAL BLOCK — {', '.join(approval.blockers)}"
        if ctx:
            record_entry_decision(
                conn,
                trade_id=None,
                market_slug=str(ctx.get("market_slug", "")),
                strategy_name=str(ctx.get("strategy_name", "")),
                side=str(ctx.get("side", "")),
                entry_price=approval.entry_price,
                approval=approval,
                allowed=False,
                block_reason=reason,
            )
            conn.commit()
        logger.warning("Live approval blocked entry:\n%s", approval.summary)
        return RiskCheckResult(allowed=False, reason=reason)

    if is_live_trading_enabled() and ctx:
        record_entry_decision(
            conn,
            trade_id=None,
            market_slug=str(ctx.get("market_slug", "")),
            strategy_name=str(ctx.get("strategy_name", "")),
            side=str(ctx.get("side", "")),
            entry_price=approval.entry_price,
            approval=approval,
            allowed=True,
        )
        conn.commit()
        logger.info("Live approval passed:\n%s", approval.summary)

    return RiskCheckResult(allowed=True)
