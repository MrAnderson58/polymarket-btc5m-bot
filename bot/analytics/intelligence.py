"""Trading Intelligence Engine — sections 31–40."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.analytics.capital_simulator import build_capital_simulator
from bot.analytics.execution_audit import build_execution_audit
from bot.analytics.false_stop import build_false_stop_detector
from bot.analytics.feature_drift import build_feature_drift
from bot.analytics.intelligence_cache import save_intelligence_cache, sections_for_report
from bot.analytics.intelligence_context import build_intelligence_context
from bot.analytics.recovery_analyzer import build_recovery_analyzer
from bot.analytics.regime_change import build_regime_change_detector
from bot.analytics.regime_engine import build_regime_engine
from bot.analytics.signal_quality import build_signal_quality
from bot.analytics.stop_quality import build_stop_quality

__all__ = [
    "build_trading_intelligence",
    "compute_trading_intelligence",
    "load_trading_intelligence",
    "save_trading_intelligence",
]


def compute_trading_intelligence(
    conn: sqlite3.Connection,
    closed: list[Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Full compute using batch MarketDataCache + trade_features (daily only)."""
    ctx = build_intelligence_context(conn, closed)
    return {
        "execution_audit": build_execution_audit(closed, ctx=ctx),
        "stop_quality": build_stop_quality(closed),
        "recovery_analyzer": build_recovery_analyzer(closed, ctx=ctx),
        "false_stop_detector": build_false_stop_detector(closed, ctx=ctx),
        "regime_engine": build_regime_engine(closed, ctx=ctx),
        "signal_quality": build_signal_quality(closed, report, ctx=ctx),
        "feature_drift": build_feature_drift(closed, report),
        "regime_change_detector": build_regime_change_detector(closed),
        "capital_simulator": build_capital_simulator(closed, report),
    }


def save_trading_intelligence(
    conn: sqlite3.Connection,
    closed: list[Any],
    report: dict[str, Any],
):
    sections = compute_trading_intelligence(conn, closed, report)
    return save_intelligence_cache(sections)


def load_trading_intelligence() -> dict[str, Any]:
    """Read-only sections for Report (no compute, no SQL)."""
    return sections_for_report()


def build_trading_intelligence(
    conn: sqlite3.Connection,
    closed: list,
    report: dict,
    *,
    compute: bool = False,
) -> dict:
    if compute:
        return compute_trading_intelligence(conn, closed, report)
    return load_trading_intelligence()
