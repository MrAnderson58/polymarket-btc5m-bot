"""S41 — News Intelligence worker (restart-safe long loop)."""

from __future__ import annotations

import logging
import signal
import time
from typing import Any

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.news_collector_n11 import run_news_update_n11
from bot.research.market_events.signal_intelligence.news_intelligence.aggregator import (
    AGGREGATION_WINDOW_SEC,
    run_news_aggregation_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.briefs import (
    BRIEF_WINDOW_SEC,
    run_global_brief_cycle_s41,
)
from bot.research.market_events.signal_intelligence.news_intelligence.reports import (
    write_period_reports_s41,
)

logger = logging.getLogger(__name__)

# Poll frequently; gate heavy work by elapsed wall time.
WORKER_POLL_SEC = 60
_shutdown = False


def _handle_sig(signum: int, frame: Any) -> None:
    global _shutdown
    logger.info("news-intel shutdown signal=%s", signum)
    _shutdown = True


def run_news_intelligence_worker_s41(
    *,
    aggregation_interval_sec: int = AGGREGATION_WINDOW_SEC,
    brief_interval_sec: int = BRIEF_WINDOW_SEC,
    poll_sec: int = WORKER_POLL_SEC,
    max_cycles: int | None = None,
    collect_rss: bool = True,
) -> dict[str, Any]:
    """Long-running Intelligence Layer worker.

    - Every ~30m: optional RSS collect + aggregate summaries
    - Every ~2h: global brief + markdown reports
    """
    global _shutdown
    _shutdown = False
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    with market_events_connection() as conn:
        apply_migrations(conn)

    last_agg = 0
    last_brief = 0
    cycles = 0
    aggregations = 0
    briefs = 0
    errors = 0

    print(
        f"news-intel-worker started agg={aggregation_interval_sec}s "
        f"brief={brief_interval_sec}s poll={poll_sec}s",
        flush=True,
    )

    while not _shutdown:
        cycles += 1
        now = int(time.time())
        try:
            if now - last_agg >= int(aggregation_interval_sec):
                if collect_rss:
                    with market_events_connection() as conn:
                        rss = run_news_update_n11(conn)
                        print(
                            f"news-intel rss inserted={rss.get('inserted')} "
                            f"fetched={rss.get('fetched')}",
                            flush=True,
                        )
                agg = run_news_aggregation_cycle_s41()
                last_agg = now
                aggregations += 1
                print(
                    f"news-intel aggregation summaries={agg.get('summaries_written')} "
                    f"deduped={agg.get('deduped')}",
                    flush=True,
                )

            if now - last_brief >= int(brief_interval_sec):
                brief = run_global_brief_cycle_s41()
                reports = write_period_reports_s41()
                last_brief = now
                briefs += 1
                print(
                    f"news-intel brief risk={brief.get('risk_level')} "
                    f"reports={reports.get('period')}",
                    flush=True,
                )
        except Exception:
            errors += 1
            logger.exception("news-intel cycle error")
            print(f"news-intel ERROR cycle={cycles}", flush=True)

        if max_cycles is not None and cycles >= max_cycles:
            break
        # Sleep in short slices for faster shutdown.
        for _ in range(max(1, int(poll_sec))):
            if _shutdown:
                break
            time.sleep(1)

    stats = {
        "cycles": cycles,
        "aggregations": aggregations,
        "briefs": briefs,
        "errors": errors,
    }
    print(f"news-intel-worker stopped {stats}", flush=True)
    return stats
