"""1h research entrypoint."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.mtf.htf_research.engine import run_htf_research
from bot.research.mtf.htf_research.framework import HtfResearchSpec
from bot.research.mtf.research_1h.config import MIN_MARKETS, MIN_TRADES_FAMILY, MIN_TRADES_VERDICT, WF_TRAIN_RATIO
from bot.research.mtf.research_1h.exits import EXIT_MODES, simulate_exit, try_entry
from bot.research.mtf.research_1h.families import FAMILIES, calibrate_thresholds
from bot.research.mtf.research_1h.features import build_obs1h
from bot.research.mtf.research_1h.models import parse_1h_window
from bot.research.mtf.research_1h.report import render_1h_report


def _1h_regime(trade) -> str:
    return getattr(trade, "regime", "unknown") or "unknown"


SPEC = HtfResearchSpec(
    timeframe="1h",
    slug_column="market_1h_slug",
    prefix="1h",
    window_fn=parse_1h_window,
    build_obs=build_obs1h,
    families=FAMILIES,
    calibrate=calibrate_thresholds,
    exit_modes=EXIT_MODES,
    simulate_exit=simulate_exit,
    try_entry=try_entry,
    min_markets=MIN_MARKETS,
    min_trades_family=MIN_TRADES_FAMILY,
    min_trades_verdict=MIN_TRADES_VERDICT,
    wf_train_ratio=WF_TRAIN_RATIO,
    ready_verdict="READY_FOR_1H_SHADOW",
    regime_fn=_1h_regime,
)


def run_1h_research(conn: sqlite3.Connection) -> dict[str, Any]:
    return run_htf_research(conn, SPEC)


__all__ = ["run_1h_research", "render_1h_report", "SPEC"]
