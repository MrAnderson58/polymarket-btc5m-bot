"""Integration tests for production telegram_messages schema mapping."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bot.database import connect, init_db
from bot.research.futures.parse_pipeline import parse_and_store_messages
from bot.research.futures.parse_sample import parse_sample
from bot.research.futures.schema import PARSE_AUDIT_TABLE, SIGNALS_TABLE, ensure_tables
from bot.research.futures.source_audit import audit_source_data
from bot.research.futures.source_data import (
    RawMessage,
    resolve_message_columns,
    row_to_raw_message,
)
from bot.research.futures.source_reader import SqliteSourceReader


PRODUCTION_DDL = """
CREATE TABLE telegram_messages (
    id BIGINT,
    channel_name TEXT,
    message_text TEXT,
    message_date TIMESTAMP,
    collected_at TIMESTAMP,
    telegram_message_id BIGINT
);
"""


def _seed_production(conn: sqlite3.Connection) -> None:
    conn.executescript(PRODUCTION_DDL)
    base = datetime(2024, 6, 1, 12, 0, 0)
    rows = [
        (1, "signalyp", "BTC LONG entry 95000 sl 94000 tp 96000", base, base, 1001),
        (2, "signalyp", "ETH SHORT sl 3500 tp 3400", datetime(2024, 6, 1, 13, 0, 0), base, 1002),
        (3, "lookonchain", "Whale moved 1000 BTC", datetime(2024, 6, 1, 14, 0, 0), base, 1003),
        (4, "signalyp", "", datetime(2024, 6, 1, 15, 0, 0), base, 1004),
    ]
    conn.executemany(
        """
        INSERT INTO telegram_messages
        (id, channel_name, message_text, message_date, collected_at, telegram_message_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


class ProductionSchemaMappingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.source_db = Path(self._tmpdir.name) / "source.db"
        self.research_db = Path(self._tmpdir.name) / "research.db"
        conn = sqlite3.connect(self.source_db)
        _seed_production(conn)
        conn.close()
        init_db(self.research_db)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _source_reader(self) -> SqliteSourceReader:
        conn = sqlite3.connect(self.source_db)
        return SqliteSourceReader(conn)

    def test_column_map_matches_production(self) -> None:
        conn = sqlite3.connect(self.source_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(telegram_messages)")]
        mapping = resolve_message_columns("telegram_messages", cols)
        self.assertIsNotNone(mapping)
        assert mapping is not None
        self.assertEqual(mapping.text_col, "message_text")
        self.assertEqual(mapping.ts_col, "message_date")
        self.assertEqual(mapping.msg_id_col, "telegram_message_id")
        self.assertEqual(mapping.source_col, "channel_name")

    def test_canonical_row_mapping(self) -> None:
        conn = sqlite3.connect(self.source_db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(telegram_messages)")]
        mapping = resolve_message_columns("telegram_messages", cols)
        row = conn.execute(
            "SELECT * FROM telegram_messages WHERE telegram_message_id = 1001"
        ).fetchone()
        msg = row_to_raw_message(dict(zip(cols, row)), mapping)
        self.assertIsInstance(msg, RawMessage)
        assert msg is not None
        self.assertEqual(msg.text, "BTC LONG entry 95000 sl 94000 tp 96000")
        self.assertEqual(msg.message_id, "1001")
        self.assertEqual(msg.source, "signalyp")
        self.assertGreater(msg.timestamp, 0)

    def test_audit_non_zero_with_sql_stats(self) -> None:
        reader = self._source_reader()
        with connect(self.research_db) as research_conn:
            ensure_tables(research_conn)
            audit = audit_source_data(research_conn, reader, source_filter="signalyp")
        reader.close()
        self.assertEqual(audit["messages"]["count"], 3)
        self.assertIsNotNone(audit["messages"]["first_timestamp"])
        self.assertIsNotNone(audit["messages"]["last_timestamp"])
        self.assertGreater(audit["messages"]["duration_seconds"], 0)
        self.assertEqual(audit["column_map"]["text"], "message_text")
        self.assertGreaterEqual(audit["messages"]["parseable_long_short"], 1)

    def test_channel_filter(self) -> None:
        reader = self._source_reader()
        msgs = list(reader.iter_telegram_messages(source="signalyp"))
        reader.close()
        self.assertEqual(len(msgs), 2)
        self.assertTrue(all(m.source == "signalyp" for m in msgs))

    def test_parse_reads_source_rows(self) -> None:
        reader = self._source_reader()
        with connect(self.research_db) as research_conn:
            ensure_tables(research_conn)
            stats = parse_and_store_messages(
                research_conn, reader, limit=100, source_filter="signalyp",
            )
            research_conn.commit()
            audit_count = research_conn.execute(
                f"SELECT COUNT(*) FROM {PARSE_AUDIT_TABLE}"
            ).fetchone()[0]
            signal_count = research_conn.execute(
                f"SELECT COUNT(*) FROM {SIGNALS_TABLE}"
            ).fetchone()[0]
        reader.close()
        self.assertEqual(stats["source_rows_read"], 3)
        self.assertEqual(stats["candidate_messages"], 2)
        self.assertGreaterEqual(stats["parsed_signals"], 1)
        self.assertEqual(audit_count, 2)
        self.assertGreaterEqual(signal_count, 1)

    def test_source_db_unmodified_after_parse(self) -> None:
        reader = self._source_reader()
        with connect(self.research_db) as research_conn:
            ensure_tables(research_conn)
            parse_and_store_messages(research_conn, reader, limit=10, source_filter="signalyp")
            research_conn.commit()
        reader.close()
        conn = sqlite3.connect(self.source_db)
        count = conn.execute("SELECT COUNT(*) FROM telegram_messages").fetchone()[0]
        conn.close()
        self.assertEqual(count, 4)

    def test_parse_sample_output(self) -> None:
        reader = self._source_reader()
        report = parse_sample(reader, source_filter="signalyp", limit=30)
        reader.close()
        self.assertEqual(report["stats"]["source_rows_read"], 3)
        self.assertGreaterEqual(report["stats"]["candidate_messages"], 2)
        self.assertTrue(report["samples"])

    def test_alternate_alias_schema(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE telegram_messages (
                id INTEGER PRIMARY KEY,
                source TEXT,
                text TEXT,
                timestamp INTEGER,
                message_id TEXT
            );
            INSERT INTO telegram_messages VALUES (1, 'ch', 'BTC LONG', 1700000000, 'ext-1');
        """)
        mapping = resolve_message_columns(
            "telegram_messages",
            [r[1] for r in conn.execute("PRAGMA table_info(telegram_messages)")],
        )
        reader = SqliteSourceReader(conn)
        msgs = list(reader.iter_telegram_messages())
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].message_id, "ext-1")


if __name__ == "__main__":
    unittest.main()
