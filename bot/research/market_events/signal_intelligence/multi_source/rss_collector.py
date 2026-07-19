"""S44 RSS collector — config-driven, failure-isolated, single-writer batching."""

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
    _http_get,
    _load_recent_titles,
    _parse_rss_items,
    insert_news_item_n11,
    is_duplicate_title_n11,
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
    """Fetch feeds independently, then one DB write batch (avoids lock storms)."""
    _configure_log()
    feeds = load_rss_feeds_from_config()
    fetched = 0
    errors = 0
    per_source: dict[str, Any] = {}
    pending: list[tuple[str, list[dict[str, Any]], float]] = []

    # Network phase — no DB held open.
    for name, url in feeds:
        t0 = time.perf_counter()
        try:
            body = _http_get(url)
            items = _parse_rss_items(body, source=name, limit=limit_per_feed)
            for it in items:
                it["source_type"] = "rss"
            fetched += len(items)
            latency = (time.perf_counter() - t0) * 1000.0
            pending.append((name, items, latency))
            per_source[name] = {"ok": True, "fetched": len(items)}
            logger.info("rss %s fetched=%s", name, len(items))
        except Exception as exc:
            errors += 1
            latency = (time.perf_counter() - t0) * 1000.0
            pending.append((name, [], latency))
            per_source[name] = {"ok": False, "error": str(exc)[:200], "fetched": 0}
            logger.warning("rss %s failed: %s", name, exc)

    inserted = 0
    with market_events_connection() as conn:
        batch = _load_recent_titles(conn)
        now = int(time.time())
        for name, items, latency in pending:
            src_ins = 0
            if per_source.get(name, {}).get("ok"):
                for item in items:
                    title = item.get("title") or ""
                    if is_duplicate_title_n11(title, batch):
                        continue
                    insert_news_item_n11(conn, item, now=now)
                    batch.insert(0, title)
                    src_ins += 1
                    inserted += 1
                record_source_health(
                    source_type="rss",
                    source_name=name,
                    status="ok" if items else "empty",
                    latency_ms=latency,
                    items=src_ins,
                    conn=conn,
                )
                per_source[name]["inserted"] = src_ins
            else:
                record_source_health(
                    source_type="rss",
                    source_name=name,
                    status="error",
                    error=str(per_source[name].get("error") or "")[:500],
                    latency_ms=latency,
                    items=0,
                    conn=conn,
                )
        conn.commit()

    return {
        "source_type": "rss",
        "feeds": len(feeds),
        "fetched": fetched,
        "inserted": inserted,
        "errors": errors,
        "per_source": per_source,
    }
