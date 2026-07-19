"""S44 RSS collector — config-driven, failure-isolated per source."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.signal_intelligence.multi_source.config_loader import (
    enabled_sources,
)
from bot.research.market_events.signal_intelligence.multi_source.health import (
    record_source_health,
)
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    _parse_rss_items,
    insert_news_item_n11,
    is_duplicate_title_n11,
    _load_recent_titles,
    _http_get,
)

logger = logging.getLogger(__name__)
LOG_PATH = BASE_DIR / "logs" / "collector-rss.log"


def _configure_log() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if any(
        isinstance(h, logging.FileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == LOG_PATH.resolve()
        for h in logger.handlers
    ):
        return
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)


def load_rss_feeds_from_config() -> list[tuple[str, str]]:
    feeds: list[tuple[str, str]] = []
    for row in enabled_sources("rss_sources"):
        name = str(row.get("name") or "").strip()
        url = str(row.get("url") or "").strip()
        if name and url:
            feeds.append((name, url))
    return feeds


def collect_rss_s44(*, limit_per_feed: int = 20) -> dict[str, Any]:
    """Fetch enabled RSS feeds independently; never abort whole run on one failure."""
    _configure_log()
    feeds = load_rss_feeds_from_config()
    inserted = 0
    fetched = 0
    errors = 0
    per_source: dict[str, Any] = {}

    with market_events_connection() as conn:
        existing = _load_recent_titles(conn)
        batch = list(existing)
        now = int(time.time())
        for name, url in feeds:
            t0 = time.perf_counter()
            try:
                body = _http_get(url)
                items = _parse_rss_items(body, source=name, limit=limit_per_feed)
                fetched += len(items)
                src_ins = 0
                for item in items:
                    title = item.get("title") or ""
                    if is_duplicate_title_n11(title, batch):
                        continue
                    item["source_type"] = "rss"
                    insert_news_item_n11(conn, item, now=now)
                    batch.insert(0, title)
                    src_ins += 1
                    inserted += 1
                conn.commit()
                latency = (time.perf_counter() - t0) * 1000.0
                record_source_health(
                    source_type="rss",
                    source_name=name,
                    status="ok",
                    latency_ms=latency,
                    items=src_ins,
                )
                per_source[name] = {"ok": True, "inserted": src_ins, "fetched": len(items)}
                logger.info("rss %s fetched=%s inserted=%s", name, len(items), src_ins)
            except Exception as exc:
                errors += 1
                latency = (time.perf_counter() - t0) * 1000.0
                record_source_health(
                    source_type="rss",
                    source_name=name,
                    status="error",
                    error=str(exc)[:500],
                    latency_ms=latency,
                    items=0,
                )
                per_source[name] = {"ok": False, "error": str(exc)[:200]}
                logger.warning("rss %s failed: %s", name, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass

    return {
        "source_type": "rss",
        "feeds": len(feeds),
        "fetched": fetched,
        "inserted": inserted,
        "errors": errors,
        "per_source": per_source,
    }
