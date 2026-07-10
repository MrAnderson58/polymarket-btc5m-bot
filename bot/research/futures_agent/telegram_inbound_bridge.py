"""Additive bridge: telegram inbound inputs → Stage 3 trader_posts (not telegram_messages)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.db import insert_telegram_research_bridge, validate_write_table
from bot.research.futures_agent.research_ingest import _insert_post
from bot.research.futures_agent.research_taxonomy import classify_research_content
from bot.research.futures_agent.research_utils import content_hash, extract_symbols

logger = logging.getLogger(__name__)

INBOUND_CHANNEL_PREFIX = "telegram_inbound"
BRIDGE_STATUS_BRIDGED = "bridged"
BRIDGE_STATUS_HASH_DUPLICATE = "hash_duplicate"
BRIDGE_STATUS_SKIPPED = "skipped"

OUTCOME_BRIDGED_NEW = "bridged_new"
OUTCOME_POSTS_REUSED = "posts_reused"
OUTCOME_ALREADY_BRIDGED = "already_bridged"
OUTCOME_CONTENT_DEDUPED = "content_deduped"
OUTCOME_SKIPPED = "skipped"
OUTCOME_ERROR = "error"


@dataclass
class BridgeResult:
    input_id: int
    post_id: int | None
    bridged: bool
    duplicate: bool
    content_hash: str
    channel_name: str
    reason: str = ""
    outcome: str = OUTCOME_SKIPPED


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


def _lookup_post_by_channel_message(
    conn: Any,
    *,
    channel: str,
    source_message_id: str,
) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM futures_agent_trader_posts
        WHERE channel_name = ? AND source_message_id = ?
        """,
        (channel, source_message_id),
    ).fetchone()
    return int(row["id"]) if row else None


def _already_bridged_result(conn: Any, input_id: int, existing: Any) -> BridgeResult:
    return BridgeResult(
        input_id=input_id,
        post_id=int(existing["post_id"]),
        bridged=False,
        duplicate=True,
        content_hash=existing["content_hash"],
        channel_name="",
        reason="already_bridged",
        outcome=OUTCOME_ALREADY_BRIDGED,
    )


def bridge_input_to_research(conn: Any, input_id: int) -> BridgeResult:
    """Idempotent bridge: futures_agent_inputs → futures_agent_trader_posts."""
    validate_write_table("futures_agent_telegram_research_bridge")

    existing = conn.execute(
        "SELECT post_id, content_hash FROM futures_agent_telegram_research_bridge WHERE input_id = ?",
        (input_id,),
    ).fetchone()
    if existing:
        return _already_bridged_result(conn, input_id, existing)

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
            outcome=OUTCOME_SKIPPED,
        )

    text = (row["raw_text"] or "").strip()
    if not text:
        return BridgeResult(
            input_id=input_id, post_id=None, bridged=False, duplicate=False,
            content_hash="", channel_name="", reason="empty_text",
            outcome=OUTCOME_SKIPPED,
        )

    detail = _parse_detail(row["status_detail"])
    chat_id = detail.get("telegram_chat_id") or _chat_id_from_source(row["source"])
    if chat_id is None:
        return BridgeResult(
            input_id=input_id, post_id=None, bridged=False, duplicate=False,
            content_hash="", channel_name="", reason="missing_chat_id",
            outcome=OUTCOME_SKIPPED,
        )

    channel = channel_for_chat(int(chat_id))
    c_hash = content_hash(text)
    message_ts = int(row["received_at"])
    source_message_id = str(row["telegram_message_id"])
    now = int(time.time())

    dup_hash = conn.execute(
        "SELECT id FROM futures_agent_trader_posts WHERE content_hash = ? LIMIT 1",
        (c_hash,),
    ).fetchone()
    if dup_hash:
        post_id = int(dup_hash["id"])
        inserted = insert_telegram_research_bridge(
            conn,
            input_id=input_id,
            post_id=post_id,
            bridge_status=BRIDGE_STATUS_HASH_DUPLICATE,
            content_hash=c_hash,
            bridged_at=now,
        )
        if not inserted:
            existing = conn.execute(
                "SELECT post_id, content_hash FROM futures_agent_telegram_research_bridge WHERE input_id = ?",
                (input_id,),
            ).fetchone()
            if existing:
                return _already_bridged_result(conn, input_id, existing)
        return BridgeResult(
            input_id=input_id,
            post_id=post_id,
            bridged=inserted,
            duplicate=True,
            content_hash=c_hash,
            channel_name=channel,
            reason="content_hash_duplicate",
            outcome=OUTCOME_CONTENT_DEDUPED if inserted else OUTCOME_ALREADY_BRIDGED,
        )

    post_id = _lookup_post_by_channel_message(
        conn, channel=channel, source_message_id=source_message_id,
    )
    post_was_new = False
    if post_id is None:
        classification = classify_research_content(text)
        symbols = extract_symbols(text)
        if row["symbol"] and row["symbol"] not in symbols:
            symbols.insert(0, row["symbol"])

        new_post_id = _insert_post(
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
        if new_post_id is not None:
            post_id = new_post_id
            post_was_new = True
        else:
            post_id = _lookup_post_by_channel_message(
                conn, channel=channel, source_message_id=source_message_id,
            )

    if post_id is None:
        return BridgeResult(
            input_id=input_id, post_id=None, bridged=False, duplicate=False,
            content_hash=c_hash, channel_name=channel, reason="insert_failed",
            outcome=OUTCOME_SKIPPED,
        )

    inserted = insert_telegram_research_bridge(
        conn,
        input_id=input_id,
        post_id=post_id,
        bridge_status=BRIDGE_STATUS_BRIDGED,
        content_hash=c_hash,
        bridged_at=now,
    )
    if not inserted:
        existing = conn.execute(
            "SELECT post_id, content_hash FROM futures_agent_telegram_research_bridge WHERE input_id = ?",
            (input_id,),
        ).fetchone()
        if existing:
            return _already_bridged_result(conn, input_id, existing)

    outcome = OUTCOME_BRIDGED_NEW if post_was_new else OUTCOME_POSTS_REUSED
    return BridgeResult(
        input_id=input_id,
        post_id=post_id,
        bridged=inserted,
        duplicate=not post_was_new,
        content_hash=c_hash,
        channel_name=channel,
        reason=BRIDGE_STATUS_BRIDGED,
        outcome=outcome if inserted else OUTCOME_ALREADY_BRIDGED,
    )
