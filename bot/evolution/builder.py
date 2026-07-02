"""Assemble evolution decision from existing caches and tables (read-only)."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.ai_agent.daily import build_daily_report
from bot.analytics.live_sample import build_live_sample
from bot.evolution.decision import decide_evolution
from bot.optimizer.cache import load_optimizer_cache
from bot.report.analytics import fetch_v2_trades, trade_pnl, _metrics
from bot.report.memory import load_experiments as load_memory_experiments
from bot.scientist.builder import build_scientist_section
from bot.scientist.experiments import load_experiments as load_scientist_experiments
from bot.scientist.scheduler import _best_next_step
from bot.strategy_review.cache import load_strategy_review_cache
from bot.trading_brain.report import build_brain_report


def load_evolution_sources(conn: sqlite3.Connection) -> dict[str, Any]:
    """Load all inputs without running optimizer/replay/scientist cycles."""
    closed = fetch_v2_trades(conn, closed_only=True)
    total_trades = _metrics([trade_pnl(t) for t in closed])["trades"]
    scientist = build_scientist_section(conn, run_cycle=False)
    scientist["best_next_step"] = _best_next_step(
        load_scientist_experiments(conn, limit=200),
        int(total_trades),
    )
    return {
        "optimizer": load_optimizer_cache(),
        "strategy_review": load_strategy_review_cache(),
        "scientist": scientist,
        "trading_brain": build_brain_report(conn),
        "ai_agent": build_daily_report(conn),
        "live_sample": build_live_sample(load_memory_experiments(), total_trades),
    }


def build_evolution(conn: sqlite3.Connection) -> dict[str, Any]:
    sources = load_evolution_sources(conn)
    return decide_evolution(sources)
