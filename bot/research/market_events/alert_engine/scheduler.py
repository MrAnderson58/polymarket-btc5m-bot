"""Phase E.5 — periodic digest/heartbeat scheduler tick."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def scheduler_tick(conn: Any, *, now: int | None = None) -> dict[str, bool]:
    """Check and send heartbeat, daily, weekly digests. Never raises."""
    import time
    from bot.research.market_events.alert_config import alerts_enabled

    results = {"heartbeat": False, "daily": False, "weekly": False}
    if not alerts_enabled():
        return results

    now = now or int(time.time())
    try:
        from bot.research.market_events.alert_engine.heartbeat import (
            send_heartbeat_telegram,
            should_send_heartbeat,
        )
        if should_send_heartbeat(conn, now=now):
            results["heartbeat"] = send_heartbeat_telegram(conn)
    except Exception as exc:
        logger.debug("heartbeat tick skipped: %s", exc)

    try:
        from bot.research.market_events.alert_engine.daily_digest import (
            send_daily_digest,
            should_send_daily,
        )
        if should_send_daily(conn, now=now):
            results["daily"] = send_daily_digest(conn, now=now)
    except Exception as exc:
        logger.debug("daily digest skipped: %s", exc)

    try:
        from bot.research.market_events.alert_engine.weekly_report import (
            send_weekly_report,
            should_send_weekly,
        )
        if should_send_weekly(conn, now=now):
            results["weekly"] = send_weekly_report(conn, now=now)
    except Exception as exc:
        logger.debug("weekly report skipped: %s", exc)

    return results


def on_shock_detected(conn: Any, *, event_id: int) -> None:
    """Research-only hooks after shock — does not affect paper trading."""
    try:
        from bot.research.market_events.alert_engine.opportunity_score import persist_opportunity_score
        persist_opportunity_score(conn, event_id=event_id)
    except Exception as exc:
        logger.debug("opportunity score skipped: %s", exc)
    try:
        from bot.research.market_events.alert_engine.timeline import persist_timeline_cache
        persist_timeline_cache(conn, event_id=event_id)
    except Exception as exc:
        logger.debug("timeline cache skipped: %s", exc)


def on_event_resolved(conn: Any, *, event_id: int) -> None:
    """Run AI comparison when paper exits complete or reversal expires."""
    try:
        from bot.research.market_events.alert_engine.ai_comparison import run_ai_comparison
        run_ai_comparison(conn, event_id=event_id)
    except Exception as exc:
        logger.debug("ai comparison skipped: %s", exc)
    try:
        from bot.research.market_events.alert_engine.timeline import persist_timeline_cache
        persist_timeline_cache(conn, event_id=event_id)
    except Exception as exc:
        logger.debug("timeline update skipped: %s", exc)
