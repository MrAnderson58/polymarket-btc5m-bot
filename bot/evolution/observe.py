"""Observe-only hook for real-time shadow evaluation.

Called from bot.main after trades close. Does NOT affect order decisions.
Records counterfactual shadow evaluations and regime shadow observations.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def observe_closed_trades(conn: sqlite3.Connection) -> None:
    """Evaluate recently-closed trades against running shadow experiments.

    Safe to call every cycle. Only processes trades not yet evaluated.
    Does NOT modify execution, strategy parameters, or order logic.
    """
    _observe_parameter_shadow(conn)
    _observe_regime_shadow(conn)


def _observe_parameter_shadow(conn: sqlite3.Connection) -> None:
    from bot.evolution.shadow_db import get_running_shadow
    from bot.evolution.shadow import sync_running_shadow_evaluations

    if get_running_shadow(conn) is None:
        return

    n = sync_running_shadow_evaluations(conn)
    if n > 0:
        logger.info("EVOLUTION_OBSERVE | parameter_shadow | evaluated %d new trade(s)", n)


def _observe_regime_shadow(conn: sqlite3.Connection) -> None:
    from bot.evolution.regime_shadow import get_running_regime_shadow, sync_regime_shadow

    if get_running_regime_shadow(conn) is None:
        return

    result = sync_regime_shadow(conn)
    if result and int(result.get("sample_size", 0)) > 0:
        logger.debug("EVOLUTION_OBSERVE | regime_shadow | sample_size=%d", result["sample_size"])
