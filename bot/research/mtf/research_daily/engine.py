"""Daily research entrypoint."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.mtf.htf_research.engine import run_htf_research
from bot.research.mtf.htf_research.framework import HtfResearchSpec
from bot.research.mtf.research_daily.config import MIN_MARKETS, MIN_TRADES_FAMILY, MIN_TRADES_VERDICT, WF_TRAIN_RATIO
from bot.research.mtf.research_daily.exits import EXIT_MODES, simulate_exit, try_entry
from bot.research.mtf.research_daily.families import FAMILIES, calibrate_thresholds
from bot.research.mtf.research_daily.features import build_obs_daily
from bot.research.mtf.research_daily.models import parse_daily_window
from bot.research.mtf.research_daily.report import render_daily_report


def _daily_regime(trade) -> str:
    return getattr(trade, "session", "unknown") or "unknown"


SPEC = HtfResearchSpec(
    timeframe="daily",
    slug_column="market_daily_slug",
    prefix="daily",
    window_fn=parse_daily_window,
    build_obs=build_obs_daily,
    families=FAMILIES,
    calibrate=calibrate_thresholds,
    exit_modes=EXIT_MODES,
    simulate_exit=simulate_exit,
    try_entry=try_entry,
    min_markets=MIN_MARKETS,
    min_trades_family=MIN_TRADES_FAMILY,
    min_trades_verdict=MIN_TRADES_VERDICT,
    wf_train_ratio=WF_TRAIN_RATIO,
    ready_verdict="READY_FOR_DAILY_SHADOW",
    regime_fn=_daily_regime,
)


def run_daily_research(conn: sqlite3.Connection) -> dict[str, Any]:
    return run_htf_research(conn, SPEC)


__all__ = ["run_daily_research", "render_daily_report", "SPEC"]
