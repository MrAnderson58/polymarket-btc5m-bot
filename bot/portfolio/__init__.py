"""Capital management and live trading safety layer."""

from bot.portfolio.approval import LiveApprovalResult, evaluate_live_approval
from bot.portfolio.guards import GuardResult, check_portfolio_guards
from bot.portfolio.kill_switch import is_kill_switch_active
from bot.portfolio.portfolio import PortfolioManager, PortfolioState
from bot.portfolio.sizing import PositionTier, effective_position_size_usdc, max_allowed_usdc

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
