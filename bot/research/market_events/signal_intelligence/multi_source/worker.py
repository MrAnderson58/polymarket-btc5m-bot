"""S44 multi-source worker."""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.multi_source.orchestrator import (
    run_multi_source_cycle_s44,
)

logger = logging.getLogger(__name__)
LOG_PATH = BASE_DIR / "logs" / "multi-source.log"
INTERVAL_SEC = 15 * 60
POLL_SEC = 60
_shutdown = False


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger(
        "bot.research.market_events.signal_intelligence.multi_source"
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
    logger.info("multi-source shutdown signal=%s", signum)
    _shutdown = True


def run_multi_source_worker_s44(
    *,
    interval_sec: int = INTERVAL_SEC,
    poll_sec: int = POLL_SEC,
    max_cycles: int | None = None,
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
    print(f"multi-source-worker started interval={interval_sec}s", flush=True)
    logger.info("multi-source-worker started interval=%ss", interval_sec)

    while not _shutdown:
        cycles += 1
        now = int(time.time())
        try:
            if now - last_run >= int(interval_sec):
                result = run_multi_source_cycle_s44()
                last_run = now
                runs += 1
                cols = result.get("collectors") or {}
                msg = (
                    "multi-source cycle "
                    + " ".join(
                        f"{k}={('ok' if (v or {}).get('ok', True) else 'ERR')}"
                        for k, v in cols.items()
                    )
                )
                logger.info(msg)
                print(msg, flush=True)
        except Exception:
            errors += 1
            logger.exception("multi-source cycle failed")
            print(f"multi-source ERROR cycle={cycles}", flush=True)

        if max_cycles is not None and cycles >= max_cycles:
            break
        for _ in range(max(1, int(poll_sec))):
            if _shutdown:
                break
            time.sleep(1)

    stats = {"cycles": cycles, "runs": runs, "errors": errors}
    print(f"multi-source-worker stopped {stats}", flush=True)
    return stats
