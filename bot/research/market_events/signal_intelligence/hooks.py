"""F.0 integration hooks — single entry from paper_runner."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def on_shock_f0(conn: Any, *, event_id: int) -> None:
    """Research-only F.0 pipeline; never affects paper eligibility."""
    from bot.research.market_events.signal_intelligence.config import F0_ENABLED, F0_AI_ENABLED

    if not F0_ENABLED:
        return

    row = conn.execute("SELECT symbol FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return
    symbol = row["symbol"]

    try:
        from bot.research.market_events.signal_intelligence.exchange_resolver import get_or_resolve
        get_or_resolve(conn, symbol)
    except Exception as exc:
        logger.debug("f0 resolver skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.exchange_context import capture_exchange_context
        capture_exchange_context(conn, event_id=event_id, symbol=symbol)
    except Exception as exc:
        logger.debug("f0 exchange context skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.multitimeframe import scan_multitimeframe
        scan_multitimeframe(conn, symbol=symbol, source_event_id=event_id)
    except Exception as exc:
        logger.debug("f0 mtf skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.exhaustion import scan_exhaustion
        scan_exhaustion(conn, symbol=symbol, source_event_id=event_id)
    except Exception as exc:
        logger.debug("f0 exhaustion skipped: %s", exc)

    try:
        from bot.research.market_events.signal_intelligence.opportunity_v2 import persist_opportunity_score_v2
        persist_opportunity_score_v2(conn, event_id=event_id)
    except Exception as exc:
        logger.debug("f0 opportunity v2 skipped: %s", exc)

    if F0_AI_ENABLED:
        try:
            from bot.research.market_events.signal_intelligence.ai_f0 import run_f0_ai_analysis
            run_f0_ai_analysis(conn, event_id)
        except Exception as exc:
            logger.debug("f0 ai skipped: %s", exc)
