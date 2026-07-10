"""Async/deferred AI analysis job queue."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from bot.research.market_events.ai_analyst.config import AI_ENABLED, AI_JOBS_PER_CYCLE, PROMPT_VERSION
from bot.research.market_events.db import insert_returning_id

logger = logging.getLogger(__name__)

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_COMPLETE = "complete"
JOB_FAILED = "failed"

_worker_lock = threading.Lock()
_worker_started = False


def enqueue_analysis_job(conn: Any, *, event_id: int, prompt_version: str = PROMPT_VERSION) -> int | None:
    """Idempotent enqueue; does not block collector on provider calls."""
    if not AI_ENABLED:
        return None
    existing = conn.execute(
        """
        SELECT id FROM market_event_analysis_jobs
        WHERE event_id = ? AND prompt_version = ?
          AND status IN (?, ?)
        """,
        (event_id, prompt_version, JOB_PENDING, JOB_RUNNING),
    ).fetchone()
    if existing:
        return int(existing["id"])
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_event_analysis_jobs (
          event_id, prompt_version, status, attempts, created_at
        ) VALUES (?, ?, ?, 0, ?)
        """,
        (event_id, prompt_version, JOB_PENDING, int(time.time())),
    )


def process_pending_jobs(conn: Any, *, max_jobs: int | None = None) -> int:
    """Process up to max_jobs analysis jobs synchronously (called off hot path)."""
    if not AI_ENABLED:
        return 0
    limit = max_jobs if max_jobs is not None else AI_JOBS_PER_CYCLE
    if limit <= 0:
        return 0
    from bot.research.market_events.ai_analyst.analysis_runner import run_analysis_job

    processed = 0
    rows = conn.execute(
        """
        SELECT id FROM market_event_analysis_jobs
        WHERE status = ? ORDER BY created_at ASC LIMIT ?
        """,
        (JOB_PENDING, limit),
    ).fetchall()
    for r in rows:
        try:
            run_analysis_job(conn, int(r["id"]))
            processed += 1
        except Exception as exc:
            logger.warning("analysis job %s failed: %s", r["id"], exc)
    return processed


def start_background_worker(db_path_factory) -> None:
    """Daemon thread for deferred AI processing; never blocks poll loop."""
    global _worker_started
    if not AI_ENABLED:
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    def _loop() -> None:
        while True:
            try:
                with db_path_factory() as conn:
                    n = process_pending_jobs(conn, max_jobs=1)
                    conn.commit()
                time.sleep(5.0 if n == 0 else 1.0)
            except Exception as exc:
                logger.debug("AI worker cycle: %s", exc)
                time.sleep(10.0)

    t = threading.Thread(target=_loop, name="ai-analyst-worker", daemon=True)
    t.start()
