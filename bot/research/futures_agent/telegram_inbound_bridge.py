"""Additive bridge: telegram inbound inputs → Stage 3 trader_posts (not telegram_messages)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.db import insert_returning_id, validate_write_table
from bot.research.futures_agent.research_ingest import _insert_post
from bot.research.futures_agent.research_taxonomy import classify_research_content
from bot.research.futures_agent.research_utils import content_hash, extract_symbols

INBOUND_CHANNEL_PREFIX = "telegram_inbound"
BRIDGE_STATUS_BRIDGED = "bridged"
BRIDGE_STATUS_HASH_DUPLICATE = "hash_duplicate"
BRIDGE_STATUS_SKIPPED = "skipped"


@dataclass
class BridgeResult:
    input_id: int
    post_id: int | None
    bridged: bool
    duplicate: bool
    content_hash: str
    channel_name: str
    reason: str = ""


def is_telegram_inbound_source(source: str) -> bool:
    return source.startswith("telegram:")


def channel_for_chat(chat_id: int) -> str:
    return f"{INBOUND_CHANNEL_PREFIX}:{chat_id}"


def _chat_id_from_source(source: str) -> int | None:
    if not is_telegram_inbound_source(source):
        return None
    try:
        return int(source.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _parse_detail(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def bridge_input_to_research(conn: Any, input_id: int) -> BridgeResult:
    """Idempotent bridge: futures_agent_inputs → futures_agent_trader_posts."""
    validate_write_table("futures_agent_telegram_research_bridge")

    existing = conn.execute(
        "SELECT post_id, content_hash FROM futures_agent_telegram_research_bridge WHERE input_id = ?",
        (input_id,),
    ).fetchone()
    if existing:
        return BridgeResult(
            input_id=input_id,
            post_id=int(existing["post_id"]),
            bridged=False,
            duplicate=True,
            content_hash=existing["content_hash"],
            channel_name="",
            reason="already_bridged",
        )

    row = conn.execute(
        """
        SELECT i.id, i.source, i.telegram_message_id, i.raw_text, i.received_at, i.status_detail,
               s.symbol, s.direction
        FROM futures_agent_inputs i
        LEFT JOIN futures_agent_signals s ON s.input_id = i.id
        WHERE i.id = ?
        """,
        (input_id,),
    ).fetchone()
    if not row or not is_telegram_inbound_source(row["source"]):
        return BridgeResult(
            input_id=input_id, post_id=None, bridged=False, duplicate=False,
            content_hash="", channel_name="", reason="not_telegram_inbound",
        )

    text = (row["raw_text"] or "").strip()
    if not text:
        return BridgeResult(
            input_id=input_id, post_id=None, bridged=False, duplicate=False,
            content_hash="", channel_name="", reason="empty_text",
        )

    detail = _parse_detail(row["status_detail"])
    chat_id = detail.get("telegram_chat_id") or _chat_id_from_source(row["source"])
    if chat_id is None:
        return BridgeResult(
            input_id=input_id, post_id=None, bridged=False, duplicate=False,
            content_hash="", channel_name="", reason="missing_chat_id",
        )

    channel = channel_for_chat(int(chat_id))
    c_hash = content_hash(text)
    message_ts = int(row["received_at"])
    source_message_id = str(row["telegram_message_id"])

    dup_hash = conn.execute(
        "SELECT id FROM futures_agent_trader_posts WHERE content_hash = ? LIMIT 1",
        (c_hash,),
    ).fetchone()
    if dup_hash:
        post_id = int(dup_hash["id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO futures_agent_telegram_research_bridge (
              input_id, post_id, bridge_status, content_hash, bridged_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (input_id, post_id, BRIDGE_STATUS_HASH_DUPLICATE, c_hash, int(time.time())),
        )
        return BridgeResult(
            input_id=input_id, post_id=post_id, bridged=False, duplicate=True,
            content_hash=c_hash, channel_name=channel, reason="content_hash_duplicate",
        )

    classification = classify_research_content(text)
    symbols = extract_symbols(text)
    if row["symbol"] and row["symbol"] not in symbols:
        symbols.insert(0, row["symbol"])

    post_id = _insert_post(
        conn,
        source_message_id=source_message_id,
        channel_name=channel,
        message_ts=message_ts,
        raw_text=text,
        c_hash=c_hash,
        content_type=classification.content_type,
        symbols=symbols,
        confidence=classification.confidence,
    )
    if post_id is None:
        existing_post = conn.execute(
            """
            SELECT id FROM futures_agent_trader_posts
            WHERE channel_name = ? AND source_message_id = ?
            """,
            (channel, source_message_id),
        ).fetchone()
        post_id = int(existing_post["id"]) if existing_post else None
        if post_id is None:
            return BridgeResult(
                input_id=input_id, post_id=None, bridged=False, duplicate=False,
                content_hash=c_hash, channel_name=channel, reason="insert_failed",
            )
        status = BRIDGE_STATUS_BRIDGED
        bridged = False
        duplicate = True
    else:
        status = BRIDGE_STATUS_BRIDGED
        bridged = True
        duplicate = False

    insert_returning_id(
        conn,
        """
        INSERT OR IGNORE INTO futures_agent_telegram_research_bridge (
          input_id, post_id, bridge_status, content_hash, bridged_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (input_id, post_id, status, c_hash, int(time.time())),
    )
    return BridgeResult(
        input_id=input_id, post_id=post_id, bridged=bridged, duplicate=duplicate,
        content_hash=c_hash, channel_name=channel, reason=status,
    )
