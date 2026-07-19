"""S42 Narrative Engine hourly worker (restart-safe)."""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.narrative_engine.engine import (
    WINDOW_SEC,
    run_narrative_engine_cycle_s42,
)

logger = logging.getLogger(__name__)

POLL_SEC = 60
LOG_PATH = BASE_DIR / "logs" / "narrative-engine.log"
_shutdown = False


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger(
        "bot.research.market_events.signal_intelligence.narrative_engine"
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
    logger.info("narrative-engine shutdown signal=%s", signum)
    _shutdown = True


def run_narrative_engine_worker_s42(
    *,
    interval_sec: int = WINDOW_SEC,
    poll_sec: int = POLL_SEC,
    max_cycles: int | None = None,
) -> dict[str, Any]:
    """Run intelligence cycle about every hour."""
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
        "narrative-engine-worker started interval=%ss poll=%ss",
        interval_sec,
        poll_sec,
    )
    print(
        f"narrative-engine-worker started interval={interval_sec}s "
        f"poll={poll_sec}s",
        flush=True,
    )

    while not _shutdown:
        cycles += 1
        now = int(time.time())
        try:
            if now - last_run >= int(interval_sec):
                result = run_narrative_engine_cycle_s42()
                last_run = now
                runs += 1
                logger.info(
                    "cycle assets=%s active=%s feed=%s",
                    result.get("assets_written"),
                    result.get("active_assets"),
                    result.get("feed_items"),
                )
                print(
                    f"narrative-engine cycle assets={result.get('assets_written')} "
                    f"active={result.get('active_assets')} "
                    f"feed={result.get('feed_items')}",
                    flush=True,
                )
        except Exception:
            errors += 1
            logger.exception("narrative-engine cycle failed")
            print(f"narrative-engine ERROR cycle={cycles}", flush=True)

        if max_cycles is not None and cycles >= max_cycles:
            break
        for _ in range(max(1, int(poll_sec))):
            if _shutdown:
                break
            time.sleep(1)

    stats = {"cycles": cycles, "runs": runs, "errors": errors}
    logger.info("narrative-engine-worker stopped %s", stats)
    print(f"narrative-engine-worker stopped {stats}", flush=True)
    return stats
