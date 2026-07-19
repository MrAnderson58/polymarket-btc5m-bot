"""S43 Event Intelligence worker."""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
    LOOKBACK_SEC,
    run_event_engine_cycle_s43,
)

logger = logging.getLogger(__name__)

POLL_SEC = 60
INTERVAL_SEC = 15 * 60
LOG_PATH = BASE_DIR / "logs" / "event-intelligence.log"
_shutdown = False


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger(
        "bot.research.market_events.signal_intelligence.event_intelligence"
    )
    if any(
        isinstance(h, logging.FileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == LOG_PATH.resolve()
        for h in root.handlers
    ):
        return
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root.addHandler(fh)


def _handle_sig(signum: int, frame: Any) -> None:
    global _shutdown
    logger.info("event-engine shutdown signal=%s", signum)
    _shutdown = True


def run_event_intelligence_worker_s43(
    *,
    interval_sec: int = INTERVAL_SEC,
    poll_sec: int = POLL_SEC,
    max_cycles: int | None = None,
    lookback_sec: int = LOOKBACK_SEC,
) -> dict[str, Any]:
    global _shutdown
    _shutdown = False
    _configure_logging()
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    with market_events_connection() as conn:
        apply_migrations(conn)

    last_run = 0
    cycles = 0
    runs = 0
    errors = 0

    logger.info(
        "event-engine-worker started interval=%ss lookback=%ss",
        interval_sec,
        lookback_sec,
    )
    print(
        f"event-engine-worker started interval={interval_sec}s "
        f"lookback={lookback_sec}s",
        flush=True,
    )

    while not _shutdown:
        cycles += 1
        now = int(time.time())
        try:
            if now - last_run >= int(interval_sec):
                result = run_event_engine_cycle_s43(lookback_sec=lookback_sec)
                last_run = now
                runs += 1
                msg = (
                    f"event-engine cycle articles={result.get('articles')} "
                    f"events={result.get('events')} created={result.get('created')} "
                    f"merged={result.get('merged')} "
                    f"duplicates_removed={result.get('duplicates_removed')} "
                    f"ms={result.get('elapsed_ms')}"
                )
                logger.info(msg)
                print(msg, flush=True)
        except Exception:
            errors += 1
            logger.exception("event-engine cycle failed")
            print(f"event-engine ERROR cycle={cycles}", flush=True)

        if max_cycles is not None and cycles >= max_cycles:
            break
        for _ in range(max(1, int(poll_sec))):
            if _shutdown:
                break
            time.sleep(1)

    stats = {"cycles": cycles, "runs": runs, "errors": errors}
    logger.info("event-engine-worker stopped %s", stats)
    print(f"event-engine-worker stopped {stats}", flush=True)
    return stats
