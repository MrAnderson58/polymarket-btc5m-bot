"""Risk limits for opening new positions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from bot.config import MAX_DAILY_LOSS_USDC, MAX_OPEN_POSITIONS
from bot.database import count_all_open_positions, daily_realized_pnl_usdc


@dataclass(frozen=True)
class RiskCheckResult:
    allowed: bool
    reason: str | None = None


def check_can_open_position(conn: sqlite3.Connection) -> RiskCheckResult:
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

    return RiskCheckResult(allowed=True)
