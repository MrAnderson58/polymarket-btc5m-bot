"""Trading Intelligence Engine — sections 31–40."""

from __future__ import annotations

from bot.analytics.capital_simulator import build_capital_simulator
from bot.analytics.execution_audit import build_execution_audit
from bot.analytics.false_stop import build_false_stop_detector
from bot.analytics.feature_drift import build_feature_drift
from bot.analytics.recovery_analyzer import build_recovery_analyzer
from bot.analytics.regime_change import build_regime_change_detector
from bot.analytics.regime_engine import build_regime_engine
from bot.analytics.signal_quality import build_signal_quality
from bot.analytics.stop_quality import build_stop_quality

__all__ = [
    "build_trading_intelligence",
]


def build_trading_intelligence(
    conn,
    closed: list,
    report: dict,
) -> dict:
    return {
        "execution_audit": build_execution_audit(conn, closed),
        "stop_quality": build_stop_quality(conn, closed),
        "recovery_analyzer": build_recovery_analyzer(conn, closed),
        "false_stop_detector": build_false_stop_detector(conn, closed),
        "regime_engine": build_regime_engine(conn, closed),
        "signal_quality": build_signal_quality(conn, closed, report),
        "feature_drift": build_feature_drift(conn, closed, report),
        "regime_change_detector": build_regime_change_detector(conn, closed),
        "capital_simulator": build_capital_simulator(closed, report),
    }
