"""Parse raw messages into futures_research_signals."""

from __future__ import annotations

import json
import sqlite3

from bot.research.futures.config import PARSER_VERSION
from bot.research.futures.parser import SignalParser
from bot.research.futures.schema import PARSE_AUDIT_TABLE, TARGETS_TABLE, insert_signal
from bot.research.futures.source_reader import SourceReader


def parse_and_store_messages(
    research_conn: sqlite3.Connection,
    source: SourceReader | None = None,
    *,
    limit: int | None = None,
) -> dict[str, int]:
    parser = SignalParser()
    stats = {"processed": 0, "inserted": 0, "partial": 0, "failed": 0}

    owns_source = source is None
    if source is None:
        from bot.research.futures.source_reader import open_source_reader
        source = open_source_reader(sqlite_conn=research_conn)

    try:
        for msg in source.iter_telegram_messages(limit=limit):
            stats["processed"] += 1
            parsed = parser.parse(msg.text)
            status = (
                "SUCCESS" if parsed.side and parsed.symbol
                else ("PARTIAL" if parsed.fields_found else "FAILED")
            )
            if status == "SUCCESS":
                stats["inserted"] += 1
            elif status == "PARTIAL":
                stats["partial"] += 1
            else:
                stats["failed"] += 1

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

            if not parsed.side and not parsed.symbol:
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
