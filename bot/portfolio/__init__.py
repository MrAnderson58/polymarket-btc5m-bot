"""Capital management and live trading safety layer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.portfolio.guards import GuardResult, check_portfolio_guards
from bot.portfolio.kill_switch import is_kill_switch_active
from bot.portfolio.portfolio import PortfolioManager, PortfolioState
from bot.portfolio.sizing import PositionTier, effective_position_size_usdc, max_allowed_usdc

if TYPE_CHECKING:
    from bot.portfolio.approval import LiveApprovalResult, evaluate_live_approval

__all__ = [
    "GuardResult",
    "LiveApprovalResult",
    "PortfolioManager",
    "PortfolioState",
    "PositionTier",
    "check_portfolio_guards",
    "effective_position_size_usdc",
    "evaluate_live_approval",
    "is_kill_switch_active",
    "max_allowed_usdc",
]


def __getattr__(name: str):
    if name in ("LiveApprovalResult", "evaluate_live_approval"):
        from bot.portfolio.approval import LiveApprovalResult, evaluate_live_approval
        globals()["LiveApprovalResult"] = LiveApprovalResult
        globals()["evaluate_live_approval"] = evaluate_live_approval
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
