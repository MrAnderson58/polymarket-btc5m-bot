"""S48 — Strategy Validation & AI Signal Ranking."""

from __future__ import annotations

from bot.research.ai_analyst.strategy_validation.daily_report import format_daily_report
from bot.research.ai_analyst.strategy_validation.dashboard import build_dashboard, format_dashboard
from bot.research.ai_analyst.strategy_validation.history import (
    SignalHistoryRecord,
    history_from_trading_signal,
)
from bot.research.ai_analyst.strategy_validation.outcomes import outcome_from_trade
from bot.research.ai_analyst.strategy_validation.ranking import (
    build_ranking_report,
    format_leaderboard,
    format_ranking,
)
from bot.research.ai_analyst.strategy_validation.service import StrategyValidationService
from bot.research.ai_analyst.strategy_validation.store import S48_VALIDATION_DDL

__all__ = [
    "S48_VALIDATION_DDL",
    "SignalHistoryRecord",
    "StrategyValidationService",
    "build_dashboard",
    "build_ranking_report",
    "format_daily_report",
    "format_dashboard",
    "format_leaderboard",
    "format_ranking",
    "history_from_trading_signal",
    "outcome_from_trade",
]
