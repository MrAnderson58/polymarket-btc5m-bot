"""S44 orchestrator — run collectors independently, then optional event merge."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from bot.research.market_events.signal_intelligence.multi_source.macro_collector import (
    collect_macro_s44,
)
from bot.research.market_events.signal_intelligence.multi_source.polymarket_collector import (
    collect_polymarket_s44,
)
from bot.research.market_events.signal_intelligence.multi_source.rss_collector import (
    collect_rss_s44,
)
from bot.research.market_events.signal_intelligence.multi_source.telegram_collector import (
    collect_telegram_s44,
)
from bot.research.market_events.signal_intelligence.multi_source.twitter_collector import (
    collect_twitter_s44,
)

logger = logging.getLogger(__name__)

CollectorFn = Callable[[], dict[str, Any]]

COLLECTORS: tuple[tuple[str, CollectorFn], ...] = (
    ("rss", collect_rss_s44),
    ("telegram", collect_telegram_s44),
    ("twitter", collect_twitter_s44),
    ("polymarket", collect_polymarket_s44),
    ("macro", collect_macro_s44),
)


def run_collector_safe(name: str, fn: CollectorFn) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        result = fn()
        result.setdefault("ok", True)
        result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        return result
    except Exception as exc:
        logger.exception("collector %s crashed", name)
        return {
            "source_type": name,
            "ok": False,
            "error": str(exc)[:500],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        }


def run_multi_source_cycle_s44(
    *,
    only: list[str] | None = None,
    run_events: bool = True,
) -> dict[str, Any]:
    """Run all collectors; one failure never stops the others."""
    selected = set(only) if only else None
    results: dict[str, Any] = {}
    for name, fn in COLLECTORS:
        if selected is not None and name not in selected:
            continue
        results[name] = run_collector_safe(name, fn)

    events_result = None
    if run_events:
        try:
            from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
                run_event_engine_cycle_s43,
            )
            events_result = run_event_engine_cycle_s43()
        except Exception as exc:
            logger.exception("event engine after multi-source failed")
            events_result = {"ok": False, "error": str(exc)[:500]}

    return {
        "collectors": results,
        "events": events_result,
        "ok": True,
    }
