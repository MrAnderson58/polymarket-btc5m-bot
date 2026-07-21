"""S47 — AI Paper Trading Engine (signals + multi-target paper execution)."""

from __future__ import annotations

from bot.research.ai_analyst.paper_trading.engine import PaperTradingEngine
from bot.research.ai_analyst.paper_trading.models import compute_stats
from bot.research.ai_analyst.paper_trading.reports import (
    dashboard_dict,
    format_closed_trades,
    format_full_report,
    format_open_trades,
    format_strategy_stats,
)
from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.paper_trading.store import (
    S47_PAPER_DDL,
    load_engine,
    persist_engine,
)

__all__ = [
    "PaperTradingEngine",
    "TradingSignal",
    "compute_stats",
    "dashboard_dict",
    "format_closed_trades",
    "format_full_report",
    "format_open_trades",
    "format_strategy_stats",
    "S47_PAPER_DDL",
    "load_engine",
    "persist_engine",
]
