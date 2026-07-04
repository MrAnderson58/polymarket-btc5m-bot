"""Observe-only hook for real-time shadow evaluation.

Called from bot.main every cycle. Does NOT affect order decisions.
Records counterfactual shadow evaluations and regime shadow observations.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

HOOK_NAME = "bot.main"


def observe_closed_trades(conn: sqlite3.Connection) -> dict[str, int]:
    """Evaluate closed trades against running shadow experiments.

    Safe to call every cycle. Only processes trades not yet evaluated.
    Does NOT modify execution, strategy parameters, or order logic.

    Returns counts: {"parameter": N, "regime": N}.
    """
    from bot.evolution.observe_stats import record_observe_run

    param_n = 0
    regime_n = 0
    error: str | None = None

    try:
        param_n = _observe_parameter_shadow(conn)
        regime_n = _observe_regime_shadow(conn)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("EVOLUTION_OBSERVE | failed: %s", error, exc_info=True)
    finally:
        record_observe_run(
            conn,
            hook=HOOK_NAME,
            parameter_evals=param_n,
            regime_evals=regime_n,
            error=error,
        )

    return {"parameter": param_n, "regime": regime_n}


def _observe_parameter_shadow(conn: sqlite3.Connection) -> int:
    from bot.evolution.shadow_db import get_running_shadow
    from bot.evolution.shadow import sync_running_shadow_evaluations

    if get_running_shadow(conn) is None:
        return 0

    n = sync_running_shadow_evaluations(conn)
    if n > 0:
        logger.info("EVOLUTION_OBSERVE | parameter_shadow | evaluated %d new trade(s)", n)
    return n


def _observe_regime_shadow(conn: sqlite3.Connection) -> int:
    from bot.evolution.regime_shadow import (
        count_pending_regime_evaluations,
        get_running_regime_shadow,
        sync_regime_shadow,
    )

    if get_running_regime_shadow(conn) is None:
        return 0

    pending_before = count_pending_regime_evaluations(conn)
    sync_regime_shadow(conn)
    pending_after = count_pending_regime_evaluations(conn)
    n = max(0, pending_before - pending_after)
    if n > 0:
        logger.info("EVOLUTION_OBSERVE | regime_shadow | evaluated %d new trade(s)", n)
    return n
