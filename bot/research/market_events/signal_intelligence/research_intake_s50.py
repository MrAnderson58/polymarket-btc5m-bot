"""S5.0 — Telegram intake for research artifacts (photo, document, URL)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import requests

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.claude_channel_s50 import telegram_claude_session
from bot.research.market_events.signal_intelligence.research_artifacts_s50 import (
    ARTIFACT_DOCUMENT,
    ARTIFACT_IMAGE,
    ARTIFACT_TEXT,
    ARTIFACT_URL,
    extract_urls,
    save_research_artifact_s50,
)
from bot.research.market_events.signal_intelligence.research_terminal_s50 import (
    dispatch_analyze_command_s50,
    run_analyze_image_s50_cli,
    run_analyze_url_s50_cli,
)

logger = logging.getLogger(__name__)

_DOC_MIMES = frozenset({
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})
_DOC_EXT = frozenset({".pdf", ".txt", ".md", ".docx"})

_ANALYZE_CAPTION_RE = re.compile(
    r"^/analyze(?:@\w+)?\s*(\S+)?",
    re.IGNORECASE,
)


def _telegram_user(message: dict[str, Any]) -> str | None:
    frm = message.get("from") or {}
    return str(frm.get("username") or frm.get("id") or "")


def is_research_document_message(message: dict[str, Any]) -> bool:
    doc = message.get("document") or {}
    if not doc:
        return False
    mime = str(doc.get("mime_type") or "").lower()
    name = str(doc.get("file_name") or "").lower()
    if mime.startswith("image/"):
        return False
    if mime in _DOC_MIMES:
        return True
    return any(name.endswith(ext) for ext in _DOC_EXT)


def download_telegram_document(
    message: dict[str, Any],
    *,
    token: str,
    dest_dir: Path | None = None,
) -> tuple[str, str, str]:
    doc = message.get("document") or {}
    file_id = doc.get("file_id")
    if not file_id:
        raise ValueError("no document")
    from bot.research.market_events.signal_intelligence.telegram_photo_g36 import _telegram_file_path

    file_path = _telegram_file_path(token, file_id)
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    resp = requests.get(url, timeout=45)
    resp.raise_for_status()
    raw = resp.content
    filename = str(doc.get("file_name") or "document.bin")
    out_dir = dest_dir or Path("data/research_intake_s50")
    out_dir.mkdir(parents=True, exist_ok=True)
    msg_id = int(message.get("message_id") or 0)
    suffix = Path(filename).suffix or ".bin"
    out_path = out_dir / f"tg_{msg_id}{suffix}"
    out_path.write_bytes(raw)
    mime = str(doc.get("mime_type") or "")
    return str(out_path), filename, mime


def _parse_analyze_caption(caption: str | None) -> str | None:
    if not caption:
        return None
    m = _ANALYZE_CAPTION_RE.match(caption.strip())
    if not m:
        return None
    sym = m.group(1)
    if sym:
        return sym.upper().replace("USDT", "")
    return None


def handle_research_image_message(
    message: dict[str, Any],
    *,
    token: str,
) -> str:
    """Save chart/image as artifact; run Claude if caption has /analyze."""
    from bot.research.market_events.signal_intelligence.telegram_photo_g36 import (
        download_telegram_image,
    )

    caption = message.get("caption")
    user = _telegram_user(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    message_id = int(message.get("message_id") or 0)
    sym = _parse_analyze_caption(str(caption) if caption else None)

    path, _b64, filename = download_telegram_image(message, token=token)

    with market_events_connection() as conn:
        apply_migrations(conn)
        artifact_id = save_research_artifact_s50(
            conn,
            artifact_type=ARTIFACT_IMAGE,
            symbol=sym,
            telegram_user=user,
            chat_id=chat_id,
            message_id=message_id,
            caption=str(caption) if caption else None,
            file_path=path,
            mime_type=filename,
        )
        conn.commit()

    if sym or (caption and "/analyze" in str(caption).lower()):
        with telegram_claude_session():
            return run_analyze_image_s50_cli(
                image_path=path,
                symbol=sym,
                caption=str(caption) if caption else None,
                artifact_id=artifact_id,
                telegram_user=user,
            )

    return (
        f"Saved research image #{artifact_id}.\n"
        f"Send with caption `/analyze BTC` for Claude chart analysis."
    )


def handle_research_document_message(
    message: dict[str, Any],
    *,
    token: str,
) -> str:
    user = _telegram_user(message)
    chat_id = int((message.get("chat") or {}).get("id") or 0)
    message_id = int(message.get("message_id") or 0)
    caption = message.get("caption")
    sym = _parse_analyze_caption(str(caption) if caption else None)

    path, filename, mime = download_telegram_document(message, token=token)
    text_preview = ""
    if mime.startswith("text/") or filename.lower().endswith((".txt", ".md")):
        try:
            text_preview = Path(path).read_text(encoding="utf-8", errors="replace")[:8000]
        except Exception:
            text_preview = ""

    with market_events_connection() as conn:
        apply_migrations(conn)
        artifact_id = save_research_artifact_s50(
            conn,
            artifact_type=ARTIFACT_DOCUMENT,
            symbol=sym,
            telegram_user=user,
            chat_id=chat_id,
            message_id=message_id,
            caption=str(caption) if caption else None,
            content_text=text_preview or None,
            file_path=path,
            mime_type=mime or filename,
        )
        conn.commit()

    return f"Saved document #{artifact_id} ({filename})."


def handle_research_url_message(
    message: dict[str, Any],
    *,
    text: str,
) -> str | None:
    """If message is primarily a URL, save and optionally analyze."""
    urls = extract_urls(text)
    if not urls:
        return None
    stripped = text.strip()
    if not stripped.startswith("http") and len(urls) != 1:
        return None
    user = _telegram_user(message)
    url = urls[0]
    with telegram_claude_session():
        return run_analyze_url_s50_cli(url=url, telegram_user=user)


def handle_research_text_message(
    message: dict[str, Any],
    *,
    text: str,
) -> str | None:
    """Free text that is not a signal — save as TEXT artifact if long research note."""
    if text.strip().startswith("/"):
        return None
    if len(text.strip()) < 40:
        return None
    user = _telegram_user(message)
    with market_events_connection() as conn:
        apply_migrations(conn)
        save_research_artifact_s50(
            conn,
            artifact_type=ARTIFACT_TEXT,
            telegram_user=user,
            chat_id=int((message.get("chat") or {}).get("id") or 0),
            message_id=int(message.get("message_id") or 0),
            content_text=text.strip()[:20000],
        )
        conn.commit()
    return None  # continue to signal inbox
