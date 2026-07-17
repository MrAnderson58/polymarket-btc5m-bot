"""S5.0 — Research artifact storage (TEXT, IMAGE, DOCUMENT, URL, NEWS)."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlparse

from bot.research.market_events.db import execute_with_retry

_TABLE = "market_events_research_artifacts_s50"

ARTIFACT_TEXT = "TEXT"
ARTIFACT_IMAGE = "IMAGE"
ARTIFACT_DOCUMENT = "DOCUMENT"
ARTIFACT_URL = "URL"
ARTIFACT_NEWS = "NEWS"

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_KNOWN_DOMAINS = (
    "x.com", "twitter.com", "coindesk.com", "binance.com",
    "tradingview.com", "t.me", "telegram.me", "github.com",
)


def extract_urls(text: str) -> list[str]:
    return _URL_RE.findall(text or "")


def classify_url_domain(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return "unknown"
    for known in _KNOWN_DOMAINS:
        if host == known or host.endswith("." + known):
            return known
    return host or "unknown"


def save_research_artifact_s50(
    conn: Any,
    *,
    artifact_type: str,
    symbol: str | None = None,
    telegram_user: str | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    caption: str | None = None,
    content_text: str | None = None,
    url: str | None = None,
    file_path: str | None = None,
    mime_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    now = int(time.time())
    source_domain = classify_url_domain(url) if url else None
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    cur = execute_with_retry(
        conn,
        f"""
        INSERT INTO {_TABLE} (
            artifact_type, symbol, telegram_user, chat_id, message_id,
            caption, content_text, url, file_path, mime_type, source_domain,
            metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_type,
            symbol.upper().replace("USDT", "") if symbol else None,
            telegram_user,
            chat_id,
            message_id,
            caption,
            content_text,
            url,
            file_path,
            mime_type,
            source_domain,
            meta_json,
            now,
        ),
    )
    return int(cur.lastrowid)


def list_research_artifacts_s50(
    conn: Any,
    *,
    symbol: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "1=1"
    if symbol:
        where += " AND symbol = ?"
        params.append(symbol.upper().replace("USDT", ""))
    params.append(max(1, min(limit, 100)))
    rows = conn.execute(
        f"""
        SELECT id, artifact_type, symbol, caption, content_text, url,
               file_path, mime_type, source_domain, telegram_user, created_at
        FROM {_TABLE}
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_research_artifact_s50(conn: Any, artifact_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {_TABLE} WHERE id = ?",
        (int(artifact_id),),
    ).fetchone()
    return dict(row) if row else None


def format_artifacts_report_s50(conn: Any, *, symbol: str | None = None, limit: int = 15) -> str:
    rows = list_research_artifacts_s50(conn, symbol=symbol, limit=limit)
    sym_label = symbol.upper().replace("USDT", "") if symbol else "ALL"
    lines = [
        f"Research Artifacts ({sym_label})",
        "",
    ]
    if not rows:
        lines.append("No artifacts yet.")
        return "\n".join(lines)
    for r in rows:
        ts = int(r.get("created_at") or 0)
        age = f"{ts}"
        cap = (r.get("caption") or r.get("content_text") or r.get("url") or "")[:60]
        lines.append(
            f"#{r['id']} {r['artifact_type']} {r.get('symbol') or '—'} "
            f"{r.get('source_domain') or ''} {cap}".strip()
        )
        lines.append(f"  user={r.get('telegram_user') or '—'} at={age}")
    return "\n".join(lines)
