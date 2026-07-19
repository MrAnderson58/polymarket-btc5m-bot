"""S44 Telegram channel collector — network then single DB write batch."""

from __future__ import annotations

import logging
import re
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
from bot.research.market_events.signal_intelligence.multi_source.http_util import (
    http_get_text,
    strip_html,
)
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    _load_recent_titles,
    insert_news_item_n11,
    is_duplicate_title_n11,
)

logger = logging.getLogger(__name__)
LOG_PATH = BASE_DIR / "logs" / "collector-telegram.log"


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


def _parse_tme_preview(html: str, *, channel: str, limit: int = 15) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    chunks = re.findall(
        r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        html,
        flags=re.I | re.S,
    )
    for raw in chunks[:limit]:
        text = strip_html(raw)
        if len(text) < 12:
            continue
        title = text[:180]
        posts.append({
            "title": title,
            "summary": text[:1500],
            "body": text[:4000],
            "source": f"tg:{channel}",
            "source_type": "telegram",
            "url": f"https://t.me/s/{channel}",
            "published_at": int(time.time()),
        })
    return posts


def collect_telegram_s44(*, limit_per_channel: int = 12) -> dict[str, Any]:
    _configure_log()
    sources = enabled_sources("telegram_sources")
    fetched = 0
    errors = 0
    per_source: dict[str, Any] = {}
    pending: list[tuple[str, list[dict[str, Any]], float, bool, str]] = []

    for row in sources:
        name = str(row.get("name") or row.get("channel") or "tg")
        channel = str(row.get("channel") or name).lstrip("@")
        t0 = time.perf_counter()
        try:
            html = http_get_text(f"https://t.me/s/{channel}", timeout=15.0)
            items = _parse_tme_preview(html, channel=channel, limit=limit_per_channel)
            fetched += len(items)
            latency = (time.perf_counter() - t0) * 1000.0
            pending.append((name, items, latency, True, ""))
            per_source[name] = {"ok": True, "fetched": len(items)}
            logger.info("telegram %s fetched=%s", name, len(items))
        except Exception as exc:
            errors += 1
            latency = (time.perf_counter() - t0) * 1000.0
            pending.append((name, [], latency, False, str(exc)[:500]))
            per_source[name] = {"ok": False, "error": str(exc)[:200]}
            logger.warning("telegram %s failed: %s", name, exc)

    inserted = 0
    with market_events_connection() as conn:
        batch = _load_recent_titles(conn)
        now = int(time.time())
        for name, items, latency, ok, err in pending:
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
                    source_type="telegram",
                    source_name=name,
                    status="ok" if items else "empty",
                    latency_ms=latency,
                    items=src_ins,
                    conn=conn,
                )
                per_source[name]["inserted"] = src_ins
            else:
                record_source_health(
                    source_type="telegram",
                    source_name=name,
                    status="error",
                    error=err,
                    latency_ms=latency,
                    conn=conn,
                )
        conn.commit()

    return {
        "source_type": "telegram",
        "channels": len(sources),
        "fetched": fetched,
        "inserted": inserted,
        "errors": errors,
        "per_source": per_source,
    }
