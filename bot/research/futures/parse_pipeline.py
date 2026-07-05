"""Parse raw messages into futures_research_signals (SQLite research DB only)."""

from __future__ import annotations

import json
import sqlite3

from bot.research.futures.config import PARSER_VERSION
from bot.research.futures.parser import SignalParser
from bot.research.futures.schema import PARSE_AUDIT_TABLE, TARGETS_TABLE, insert_signal
from bot.research.futures.source_reader import SourceReader, resolve_research_source


def _classify(parsed) -> tuple[str, bool]:
    if parsed.side and parsed.symbol:
        return "SUCCESS", True
    if parsed.fields_found:
        return "PARTIAL", bool(parsed.side or parsed.symbol)
    return "FAILED", False


def parse_and_store_messages(
    research_conn: sqlite3.Connection,
    source: SourceReader | None = None,
    *,
    source_conn: sqlite3.Connection | None = None,
    limit: int | None = None,
    source_filter: str | None = None,
) -> dict[str, int]:
    parser = SignalParser()
    stats = {
        "source_rows_read": 0,
        "candidate_messages": 0,
        "parsed_signals": 0,
        "inserted": 0,
        "partial": 0,
        "failed": 0,
        "skipped_non_signal": 0,
        "skipped_invalid_row": 0,
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
            parsed = parser.parse(msg.text)
            status, is_signal = _classify(parsed)

            if status == "SUCCESS":
                stats["inserted"] += 1
                stats["parsed_signals"] += 1
            elif status == "PARTIAL":
                stats["partial"] += 1
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
                    msg.source, msg.message_id, PARSER_VERSION, msg.text,
                    status, json.dumps(parsed.fields_found), json.dumps(parsed.errors),
                ),
            )

            if not is_signal:
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
                "parser_version": PARSER_VERSION,
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
