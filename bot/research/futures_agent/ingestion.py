"""Fresh signal ingestion — immutable raw storage first."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.config import (
    INPUT_TYPE_CLI,
    INPUT_TYPE_FORWARDED,
    STATUS_RECEIVED,
)
from bot.research.futures_agent.db import insert_returning_id, validate_write_table as _validate


@dataclass
class IngestResult:
    input_id: int
    duplicate: bool
    telegram_message_id: str | None


def ingest_forwarded_signal(
    conn: Any,
    *,
    raw_text: str,
    source: str = "forwarded",
    telegram_message_id: str | None = None,
    raw_message_id: int | None = None,
    input_type: str = INPUT_TYPE_FORWARDED,
    received_at: int | None = None,
) -> IngestResult:
    """Store immutable raw forwarded message. Never UPDATE raw_text."""
    _validate("futures_agent_inputs")
    text = raw_text.strip()
    if not text:
        raise ValueError("empty signal text")

    ts = received_at or int(time.time())
    tg_id = telegram_message_id or f"local-{ts}-{hash(text) & 0xFFFFFF}"

    existing = conn.execute(
        """
        SELECT id FROM futures_agent_inputs
        WHERE source = ? AND telegram_message_id = ?
        """,
        (source, tg_id),
    ).fetchone()
    if existing:
        return IngestResult(int(existing["id"]), True, tg_id)

    input_id = insert_returning_id(
        conn,
        """
        INSERT INTO futures_agent_inputs (
            source, telegram_message_id, raw_message_id, raw_text,
            received_at, input_type, processing_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source, tg_id, raw_message_id, text, ts, input_type, STATUS_RECEIVED),
    )
    return IngestResult(input_id, False, tg_id)


def ingest_from_cli(conn: Any, raw_text: str) -> IngestResult:
    return ingest_forwarded_signal(
        conn, raw_text=raw_text, source="cli", input_type=INPUT_TYPE_CLI,
    )
