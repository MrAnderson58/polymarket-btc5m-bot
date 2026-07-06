"""Process ingested signals — classify, parse, gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bot.research.futures.parser_v2 import PARSER_VERSION_V2, parse_signal_v2
from bot.research.futures.taxonomy import MessageType
from bot.research.futures_agent.config import (
    PARSER_VERSION,
    PARSE_STATUS_FAILED,
    PARSE_STATUS_NEEDS_REVIEW,
    PARSE_STATUS_PARTIAL,
    PARSE_STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_PARSED,
    STATUS_PENDING_SNAPSHOT,
    STATUS_RECEIVED,
    STATUS_REJECTED,
)
from bot.research.futures_agent.db import insert_returning_id, validate_write_table


@dataclass
class ProcessResult:
    input_id: int
    signal_id: int | None
    parse_status: str
    processing_status: str
    passes_gate: bool
    taxonomy: str
    gate_reason: str


def _map_parse_status(result, passes_gate: bool) -> str:
    if passes_gate:
        return PARSE_STATUS_SUCCESS
    if result.parsed.side or result.parsed.symbol:
        if result.message_type == MessageType.EXPLICIT_SIGNAL:
            return PARSE_STATUS_NEEDS_REVIEW
        return PARSE_STATUS_PARTIAL
    return PARSE_STATUS_FAILED


def _map_processing_status(taxonomy: MessageType, passes_gate: bool, parse_status: str) -> str:
    if passes_gate:
        return STATUS_PENDING_SNAPSHOT
    if parse_status == PARSE_STATUS_NEEDS_REVIEW:
        return STATUS_NEEDS_REVIEW
    if taxonomy in (
        MessageType.MARKET_REVIEW,
        MessageType.MARKET_COMMENTARY,
        MessageType.NEWS,
        MessageType.PROMO,
        MessageType.OTHER,
        MessageType.TP_HIT,
        MessageType.SL_HIT,
        MessageType.POSITION_CLOSE,
        MessageType.TRADE_UPDATE,
    ):
        return STATUS_REJECTED
    if parse_status == PARSE_STATUS_FAILED:
        return STATUS_REJECTED
    return STATUS_NEEDS_REVIEW


def process_input(conn: Any, input_id: int) -> ProcessResult:
    validate_write_table("futures_agent_signals")
    row = conn.execute(
        "SELECT id, raw_text, processing_status FROM futures_agent_inputs WHERE id = ?",
        (input_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"input {input_id} not found")

    existing = conn.execute(
        "SELECT id FROM futures_agent_signals WHERE input_id = ?",
        (input_id,),
    ).fetchone()
    if existing:
        return ProcessResult(
            input_id=input_id,
            signal_id=int(existing["id"]),
            parse_status="EXISTING",
            processing_status=row["processing_status"],
            passes_gate=False,
            taxonomy="",
            gate_reason="already_processed",
        )

    text = row["raw_text"]
    result = parse_signal_v2(text)
    parsed = result.parsed
    passes_gate = result.passes_gate
    parse_status = _map_parse_status(result, passes_gate)
    processing_status = _map_processing_status(
        result.message_type, passes_gate, parse_status,
    )

    parse_json = {
        "fields_found": parsed.fields_found,
        "errors": parsed.errors,
        "taxonomy_reasons": result.taxonomy_reasons,
        "gate_reason": result.gate_reason,
        "parser_confidence": parsed.parser_confidence,
        "take_profits": parsed.take_profits,
    }

    signal_id = insert_returning_id(
        conn,
        """
        INSERT INTO futures_agent_signals (
            input_id, parser_version, taxonomy, symbol, direction,
            entry_low, entry_high, stop_loss, leverage, timeframe,
            explicit_confidence, parse_status, passes_gate, gate_reason, parse_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            input_id,
            PARSER_VERSION_V2,
            result.message_type.value,
            parsed.symbol,
            parsed.side,
            parsed.entry_min,
            parsed.entry_max,
            parsed.stop_loss,
            parsed.leverage,
            parsed.timeframe,
            parsed.confidence,
            parse_status,
            1 if passes_gate else 0,
            result.gate_reason,
            json.dumps(parse_json),
        ),
    )

    for i, tp in enumerate(parsed.take_profits, start=1):
        conn.execute(
            """
            INSERT INTO futures_agent_targets (signal_id, target_index, target_price)
            VALUES (?, ?, ?)
            """,
            (signal_id, i, tp),
        )

    conn.execute(
        """
        UPDATE futures_agent_inputs
        SET processing_status = ?, status_detail = ?
        WHERE id = ?
        """,
        (processing_status, result.gate_reason, input_id),
    )

    return ProcessResult(
        input_id=input_id,
        signal_id=signal_id,
        parse_status=parse_status,
        processing_status=processing_status,
        passes_gate=passes_gate,
        taxonomy=result.message_type.value,
        gate_reason=result.gate_reason,
    )


def process_pending(conn: Any, *, limit: int = 50) -> list[ProcessResult]:
    rows = conn.execute(
        """
        SELECT id FROM futures_agent_inputs
        WHERE processing_status = ?
        ORDER BY received_at ASC
        LIMIT ?
        """,
        (STATUS_RECEIVED, limit),
    ).fetchall()
    return [process_input(conn, int(r["id"])) for r in rows]
