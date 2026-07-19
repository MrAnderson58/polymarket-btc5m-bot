"""S44 Macro collector (Fed RSS + series snapshots)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import execute_with_retry, market_events_connection
from bot.research.market_events.signal_intelligence.multi_source.config_loader import (
    enabled_sources,
)
from bot.research.market_events.signal_intelligence.multi_source.health import (
    record_source_health,
)
from bot.research.market_events.signal_intelligence.news_collector_n11 import (
    _http_get,
    _parse_rss_items,
)

logger = logging.getLogger(__name__)
LOG_PATH = BASE_DIR / "logs" / "collector-macro.log"

# Lightweight public placeholders when no paid feed is configured.
_SERIES_NOTES = {
    "CPI": "US CPI inflation print watch",
    "PPI": "US PPI producer prices watch",
    "NFP": "US Non-Farm Payrolls watch",
    "DXY": "US Dollar Index (DXY) watch",
    "US10Y": "US 10Y Treasury yield watch",
    "GOLD": "Gold spot macro watch",
    "OIL": "Crude oil macro watch",
}


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


def collect_macro_s44() -> dict[str, Any]:
    _configure_log()
    sources = enabled_sources("macro_sources")
    inserted = 0
    errors = 0
    per_source: dict[str, Any] = {}
    now = int(time.time())

    with market_events_connection() as conn:
        for row in sources:
            name = str(row.get("name") or "macro")
            kind = str(row.get("type") or "series").lower()
            t0 = time.perf_counter()
            try:
                title = name
                summary = ""
                value = None
                unit = None
                if kind == "rss":
                    url = str(row.get("url") or "")
                    body = _http_get(url)
                    items = _parse_rss_items(body, source=name, limit=3)
                    if items:
                        title = items[0].get("title") or name
                        summary = items[0].get("summary") or title
                    else:
                        summary = f"{name} RSS empty"
                else:
                    series_id = str(row.get("series_id") or name).upper()
                    summary = _SERIES_NOTES.get(series_id, f"{series_id} macro watch")
                    title = f"{series_id} macro watch"
                    # Snapshot marker (no paid data vendor in intel layer).
                    value = None
                    unit = "watch"

                execute_with_retry(
                    conn,
                    """
                    INSERT INTO market_macro_events (
                      created_at, name, event_type, title, summary,
                      value, unit, tags_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        name,
                        kind,
                        str(title)[:300],
                        str(summary)[:2000],
                        value,
                        unit,
                        json.dumps(row.get("tags") or [], ensure_ascii=False),
                        json.dumps(row, ensure_ascii=False, default=str)[:4000],
                    ),
                )
                inserted += 1
                conn.commit()
                latency = (time.perf_counter() - t0) * 1000.0
                record_source_health(
                    source_type="macro",
                    source_name=name,
                    status="ok",
                    latency_ms=latency,
                    items=1,
                )
                per_source[name] = {"ok": True, "title": str(title)[:100]}
                logger.info("macro %s ok title=%s", name, str(title)[:80])
            except Exception as exc:
                errors += 1
                latency = (time.perf_counter() - t0) * 1000.0
                record_source_health(
                    source_type="macro",
                    source_name=name,
                    status="error",
                    error=str(exc)[:500],
                    latency_ms=latency,
                )
                per_source[name] = {"ok": False, "error": str(exc)[:200]}
                logger.warning("macro %s failed: %s", name, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass

    return {
        "source_type": "macro",
        "sources": len(sources),
        "inserted": inserted,
        "errors": errors,
        "per_source": per_source,
    }


def load_recent_macro_as_articles(
    conn: Any, *, since_ts: int, limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, created_at, name, event_type, title, summary, tags_json
        FROM market_macro_events
        WHERE created_at >= ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (since_ts, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": f"macro-{r['id']}",
            "title": str(r["title"] or r["name"])[:300],
            "summary": str(r["summary"] or "")[:1500],
            "body": str(r["summary"] or "")[:4000],
            "source": f"macro:{r['name']}",
            "source_type": "macro",
            "timestamp": int(r["created_at"] or 0),
            "published_at": int(r["created_at"] or 0),
            "symbols": [],
        })
    return out
