"""Trade Intelligence V1 — knowledge layer foundation (no LLM, no trading logic)."""

from __future__ import annotations

from bot.research.market_events.trade_intelligence.cli import run_trade_cli
from bot.research.market_events.trade_intelligence.knowledge import build_trade_knowledge
from bot.research.market_events.trade_intelligence.models import TradeKnowledge, TradeRecord
from bot.research.market_events.trade_intelligence.schema import ensure_trade_intelligence_schema

__all__ = [
    "TradeKnowledge",
    "TradeRecord",
    "build_trade_knowledge",
    "ensure_trade_intelligence_schema",
    "run_trade_cli",
]
