"""S41 — News Intelligence Layer (watchlist, tagging, aggregation, briefs)."""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.news_intelligence.aggregator import (
    run_news_aggregation_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.briefs import (
    run_global_brief_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.reports import (
    write_period_reports_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.watchlist import (
    detect_symbols,
    load_watchlist,
    watched_symbols,
)
from bot.research.market_events.signal_intelligence.news_intelligence.worker import (
    run_news_intelligence_worker_s41,
)

__all__ = [
    "detect_symbols",
    "load_watchlist",
    "run_global_brief_cycle_s41",
    "run_news_aggregation_cycle_s41",
    "run_news_intelligence_worker_s41",
    "watched_symbols",
    "write_period_reports_s41",
]
