"""Standalone AI analysis worker loop (separate from shock-paper embedded thread)."""

from __future__ import annotations

import logging
import signal
import time

from bot.research.market_events.ai_analyst.config import AI_ENABLED, AI_JOBS_PER_CYCLE
from bot.research.market_events.ai_analyst.job_queue import process_pending_jobs
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.startup_lock import market_events_startup_lock

logger = logging.getLogger(__name__)
_shutdown = False


def _handle_sig(signum, frame) -> None:
    global _shutdown
    logger.info("ai-worker shutdown signal %s", signum)
    _shutdown = True


def run_ai_worker(*, poll_sec: float = 5.0) -> None:
    """Process pending AI jobs until SIGINT/SIGTERM."""
    logging.basicConfig(level=logging.INFO, format="[ai-worker] %(message)s")
    if not AI_ENABLED:
        logger.warning("ME_AI_ANALYST_ENABLED=false — worker idle (enable to process jobs)")
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    with market_events_startup_lock():
        with market_events_connection() as conn:
            apply_migrations(conn)

    logger.info("started poll_sec=%s jobs_per_cycle=%s", poll_sec, AI_JOBS_PER_CYCLE or 1)
    while not _shutdown:
        try:
            with market_events_connection() as conn:
                n = process_pending_jobs(conn, max_jobs=AI_JOBS_PER_CYCLE or 1)
                conn.commit()
            time.sleep(1.0 if n else poll_sec)
        except Exception as exc:
            logger.error("cycle error: %s", exc)
            time.sleep(poll_sec)
    logger.info("stopped")
