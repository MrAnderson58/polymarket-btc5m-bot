"""Parse raw messages into futures_research_signals (SQLite research DB only)."""

from __future__ import annotations

import json
import sqlite3

from bot.research.futures.config import PARSER_VERSION, PARSER_VERSION_V2
from bot.research.futures.parser_factory import get_parser
from bot.research.futures.schema import PARSE_AUDIT_TABLE, TARGETS_TABLE, insert_signal
from bot.research.futures.source_reader import SourceReader, resolve_research_source


def _process_v1(parsed) -> tuple[str, bool, bool]:
    if parsed.side and parsed.symbol:
        return "SUCCESS", True, True
    if parsed.fields_found:
        return "PARTIAL", bool(parsed.side or parsed.symbol), False
    return "FAILED", False, False


def _process_v2(result) -> tuple[str, bool, bool]:
    parsed = result.parsed
    if result.passes_gate:
        return "SUCCESS", True, True
    if parsed.fields_found:
        return "PARTIAL", False, False
    return "FAILED", False, False


def parse_and_store_messages(
    research_conn: sqlite3.Connection,
    source: SourceReader | None = None,
    *,
    source_conn: sqlite3.Connection | None = None,
    limit: int | None = None,
    source_filter: str | None = None,
    parser_version: str | None = None,
) -> dict[str, int]:
    version = parser_version or PARSER_VERSION
    parser = get_parser(version)
    stats = {
        "parser_version": version,
        "source_rows_read": 0,
        "candidate_messages": 0,
        "parsed_signals": 0,
        "inserted": 0,
        "partial": 0,
        "failed": 0,
        "skipped_non_signal": 0,
        "skipped_invalid_row": 0,
        "skipped_gate": 0,
    }

    source, owns_source = resolve_research_source(
        research_conn, source=source, source_conn=source_conn,
    )

    try:
        for row in source.iter_raw_rows(limit=limit, source=source_filter):
            stats["source_rows_read"] += 1
            msg = source.map_row(row)
            if msg is None:
                stats["skipped_invalid_row"] += 1
                continue

            stats["candidate_messages"] += 1

            if version == PARSER_VERSION_V2:
                result = parser.parse(msg.text)
                status, store_signal, _ = _process_v2(result)
                parsed = result.parsed
                if status == "PARTIAL" and not result.passes_gate:
                    stats["skipped_gate"] += 1
                fields = parsed.fields_found + [f"taxonomy:{result.message_type.value}"]
                errors = parsed.errors + [result.gate_reason] if result.gate_reason else parsed.errors
            else:
                parsed = parser.parse(msg.text)
                status, store_signal, is_partial_signal = _process_v1(parsed)
                fields = parsed.fields_found
                errors = parsed.errors

            if status == "SUCCESS":
                stats["inserted"] += 1
                stats["parsed_signals"] += 1
            elif status == "PARTIAL":
                stats["partial"] += 1
                if version != PARSER_VERSION_V2:
                    stats["parsed_signals"] += 1
            else:
                stats["failed"] += 1
                stats["skipped_non_signal"] += 1

            research_conn.execute(
                f"""
                INSERT OR REPLACE INTO {PARSE_AUDIT_TABLE} (
                    source, message_id, parser_version, raw_text,
                    parse_status, fields_found, errors
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.source, msg.message_id, version, msg.text,
                    status, json.dumps(fields), json.dumps(errors),
                ),
            )

            if not store_signal:
                continue

            sid = insert_signal(research_conn, {
                "source": msg.source,
                "message_id": msg.message_id,
                "timestamp": msg.timestamp,
                "symbol": parsed.symbol,
                "side": parsed.side,
                "entry_min": parsed.entry_min,
                "entry_max": parsed.entry_max,
                "stop_loss": parsed.stop_loss,
                "leverage": parsed.leverage,
                "timeframe": parsed.timeframe,
                "confidence": parsed.confidence,
                "raw_text": msg.text,
                "parser_confidence": parsed.parser_confidence,
                "parser_version": version,
            })
            if sid:
                research_conn.execute(f"DELETE FROM {TARGETS_TABLE} WHERE signal_id = ?", (sid,))
                for i, tp in enumerate(parsed.take_profits, start=1):
                    research_conn.execute(
                        f"""
                        INSERT INTO {TARGETS_TABLE} (signal_id, level_index, price, label)
                        VALUES (?, ?, ?, ?)
                        """,
                        (sid, i, tp, f"TP{i}"),
                    )
    finally:
        if owns_source:
            source.close()

    return stats
