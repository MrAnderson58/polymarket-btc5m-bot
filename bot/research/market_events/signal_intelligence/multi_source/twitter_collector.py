"""S44 X/Twitter whitelist collector — network then single DB write batch."""

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
LOG_PATH = BASE_DIR / "logs" / "collector-twitter.log"

_NITTER_BASES = (
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
)


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


def _fetch_user_rss(username: str, *, limit: int = 12) -> list[dict[str, Any]]:
    user = username.lstrip("@")
    last_err: Exception | None = None
    for base in _NITTER_BASES:
        url = f"{base}/{user}/rss"
        try:
            body = _http_get(url, timeout=10.0)
            items = _parse_rss_items(body, source=f"x:{user}", limit=limit)
            if items:
                for it in items:
                    it["source_type"] = "twitter"
                    it["source"] = f"x:{user}"
                return items
        except Exception as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err
    return []


def collect_twitter_s44(*, limit_per_user: int = 12) -> dict[str, Any]:
    _configure_log()
    sources = enabled_sources("twitter_sources")
    fetched = 0
    errors = 0
    per_source: dict[str, Any] = {}
    pending: list[tuple[str, list[dict[str, Any]], float, bool, str]] = []

    for row in sources:
        user = str(row.get("username") or "").lstrip("@")
        if not user:
            continue
        t0 = time.perf_counter()
        try:
            items = _fetch_user_rss(user, limit=limit_per_user)
            fetched += len(items)
            latency = (time.perf_counter() - t0) * 1000.0
            pending.append((user, items, latency, True, ""))
            per_source[user] = {"ok": True, "fetched": len(items)}
            logger.info("twitter %s fetched=%s", user, len(items))
        except Exception as exc:
            errors += 1
            latency = (time.perf_counter() - t0) * 1000.0
            pending.append((user, [], latency, False, str(exc)[:500]))
            per_source[user] = {"ok": False, "error": str(exc)[:200]}
            logger.warning("twitter %s failed: %s", user, exc)

    inserted = 0
    with market_events_connection() as conn:
        batch = _load_recent_titles(conn)
        now = int(time.time())
        for user, items, latency, ok, err in pending:
            src_ins = 0
            if ok:
                for item in items:
                    title = item.get("title") or ""
                    if is_duplicate_title_n11(title, batch):
                        continue
                    insert_news_item_n11(conn, item, now=now)
                    batch.insert(0, title)
                    src_ins += 1
                    inserted += 1
                record_source_health(
                    source_type="twitter",
                    source_name=user,
                    status="ok" if items else "empty",
                    latency_ms=latency,
                    items=src_ins,
                    conn=conn,
                )
                per_source[user]["inserted"] = src_ins
            else:
                record_source_health(
                    source_type="twitter",
                    source_name=user,
                    status="error",
                    error=err,
                    latency_ms=latency,
                    conn=conn,
                )
        conn.commit()

    return {
        "source_type": "twitter",
        "users": len(sources),
        "fetched": fetched,
        "inserted": inserted,
        "errors": errors,
        "per_source": per_source,
    }
