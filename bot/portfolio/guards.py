"""Daily loss guard and circuit breaker."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from bot.config import (
    CIRCUIT_BREAKER_PAUSE_SEC,
    CIRCUIT_BREAKER_STOPS,
    CONSECUTIVE_STOPS_DAILY_PAUSE,
    PORTFOLIO_DAILY_LOSS_LIMIT_USDC,
)
from bot.database import daily_realized_pnl_usdc
from bot.portfolio.portfolio import PortfolioManager


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    paused: bool = False
    reason: str | None = None
    pause_until_ts: int | None = None


def _consecutive_stop_losses(conn: sqlite3.Connection, *, limit: int = 10) -> int:
    rows = conn.execute(
        """
        SELECT exit_reason
        FROM early_reversion_v2_trades
        WHERE status = 'closed'
        ORDER BY closed_at DESC, entry_ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    streak = 0
    for row in rows:
        if row["exit_reason"] == "STOP_LOSS":
            streak += 1
        else:
            break
    return streak


def check_portfolio_guards(conn: sqlite3.Connection) -> GuardResult:
    manager = PortfolioManager.load(conn)
    manager.refresh(conn)
    now_ts = int(time.time())

    if manager.state.pause_until_ts and manager.state.pause_until_ts > now_ts:
        return GuardResult(
            allowed=False,
            paused=True,
            reason=manager.state.pause_reason or "TRADING PAUSED",
            pause_until_ts=manager.state.pause_until_ts,
        )

    daily_pnl = daily_realized_pnl_usdc(conn)
    stop_streak = _consecutive_stop_losses(conn)

    if daily_pnl <= -PORTFOLIO_DAILY_LOSS_LIMIT_USDC:
        manager.pause(
            conn,
            reason="TRADING PAUSED — daily loss",
            detail=f"daily_pnl={daily_pnl:+.2f} limit={PORTFOLIO_DAILY_LOSS_LIMIT_USDC:.2f}",
        )
        return GuardResult(
            allowed=False,
            paused=True,
            reason="TRADING PAUSED — daily loss",
        )

    if stop_streak >= CONSECUTIVE_STOPS_DAILY_PAUSE:
        manager.pause(
            conn,
            reason="TRADING PAUSED — daily loss",
            detail=f"{stop_streak} consecutive stop losses",
        )
        return GuardResult(
            allowed=False,
            paused=True,
            reason="TRADING PAUSED — daily loss",
        )

    if stop_streak >= CIRCUIT_BREAKER_STOPS:
        manager.pause(
            conn,
            reason="TRADING PAUSED — circuit breaker",
            detail=f"{stop_streak} consecutive stop losses",
            pause_sec=CIRCUIT_BREAKER_PAUSE_SEC,
        )
        return GuardResult(
            allowed=False,
            paused=True,
            reason="TRADING PAUSED — circuit breaker",
            pause_until_ts=manager.state.pause_until_ts,
        )

    manager.save(conn)
    return GuardResult(allowed=True)
