"""S44 Telegram channel collector (public preview pages; failure-isolated)."""

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
    """Best-effort parse of public t.me/s/<channel> preview HTML."""
    posts: list[dict[str, Any]] = []
    # Messages often in divs with class tgme_widget_message_text
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
    inserted = 0
    fetched = 0
    errors = 0
    per_source: dict[str, Any] = {}

    with market_events_connection() as conn:
        batch = _load_recent_titles(conn)
        now = int(time.time())
        for row in sources:
            name = str(row.get("name") or row.get("channel") or "tg")
            channel = str(row.get("channel") or name).lstrip("@")
            t0 = time.perf_counter()
            try:
                url = f"https://t.me/s/{channel}"
                html = http_get_text(url, timeout=15.0)
                items = _parse_tme_preview(
                    html, channel=channel, limit=limit_per_channel,
                )
                fetched += len(items)
                src_ins = 0
                for item in items:
                    title = item.get("title") or ""
                    if is_duplicate_title_n11(title, batch):
                        continue
                    insert_news_item_n11(conn, item, now=now)
                    batch.insert(0, title)
                    src_ins += 1
                    inserted += 1
                conn.commit()
                latency = (time.perf_counter() - t0) * 1000.0
                status = "ok" if items else "empty"
                record_source_health(
                    source_type="telegram",
                    source_name=name,
                    status=status,
                    latency_ms=latency,
                    items=src_ins,
                )
                per_source[name] = {
                    "ok": True, "inserted": src_ins, "fetched": len(items),
                }
                logger.info(
                    "telegram %s fetched=%s inserted=%s", name, len(items), src_ins,
                )
            except Exception as exc:
                errors += 1
                latency = (time.perf_counter() - t0) * 1000.0
                record_source_health(
                    source_type="telegram",
                    source_name=name,
                    status="error",
                    error=str(exc)[:500],
                    latency_ms=latency,
                )
                per_source[name] = {"ok": False, "error": str(exc)[:200]}
                logger.warning("telegram %s failed: %s", name, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass

    return {
        "source_type": "telegram",
        "channels": len(sources),
        "fetched": fetched,
        "inserted": inserted,
        "errors": errors,
        "per_source": per_source,
    }
