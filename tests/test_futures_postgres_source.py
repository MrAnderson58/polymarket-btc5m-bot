"""Tests for PostgreSQL futures source adapter."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.database import connect, init_db
from bot.research.futures.db_config import (
    REASON_POSTGRES_CONNECT_FAILED,
    REASON_POSTGRES_URL_MISSING,
    REASON_SOURCE_BACKEND_MISMATCH,
)
from bot.research.futures.source_audit import audit_source_data, check_source_connection
from bot.research.futures.source_reader import (
    PostgresSourceReader,
    SourceConfigError,
    SqliteSourceReader,
    open_source_reader,
)


class PostgresSourceAdapterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _mock_pg_conn(self) -> MagicMock:
        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        return conn

    @patch.dict(os.environ, {
        "FUTURES_SOURCE_BACKEND": "postgres",
        "FUTURES_SOURCE_DATABASE_URL": "postgresql://user:pass@localhost:5432/telegram",
    })
    @patch("bot.research.futures.source_reader._postgres_connect")
    def test_postgres_reader_lists_tables(self, mock_connect: MagicMock) -> None:
        mock_connect.return_value = MagicMock()

        reader = PostgresSourceReader("postgresql://user:pass@localhost:5432/telegram")
        with patch.object(reader, "_pg_table_exists", side_effect=lambda n: n == "telegram_messages"):
            with patch.object(reader, "_pg_count", return_value=7520):
                with patch.object(reader, "_pg_columns", return_value=["message_id", "text", "timestamp"]):
                    with patch.object(reader, "_pg_ts_range", return_value=(1700000000, 1710000000)):
                        tables = reader.list_source_tables()
        reader.close()

        self.assertEqual(reader.info.backend, "postgres")
        self.assertTrue(tables["telegram_messages"]["exists"])
        self.assertEqual(tables["telegram_messages"]["row_count"], 7520)

    @patch.dict(os.environ, {
        "FUTURES_SOURCE_BACKEND": "postgres",
        "FUTURES_SOURCE_DATABASE_URL": "",
    }, clear=True)
    def test_missing_postgres_url_raises(self) -> None:
        with connect(self.db_path) as conn:
            with self.assertRaises(SourceConfigError) as ctx:
                open_source_reader(sqlite_conn=conn)
        self.assertEqual(ctx.exception.reason_code, REASON_POSTGRES_URL_MISSING)

    @patch.dict(os.environ, {
        "FUTURES_SOURCE_BACKEND": "postgres",
        "FUTURES_SOURCE_DATABASE_URL": "postgresql://user:pass@localhost:5432/telegram",
        "FUTURES_REQUIRE_POSTGRES": "true",
    })
    @patch("bot.research.futures.source_reader._postgres_connect", side_effect=OSError("connection refused"))
    def test_postgres_connect_failure_no_silent_sqlite(self, _mock: MagicMock) -> None:
        with connect(self.db_path) as conn:
            with self.assertRaises(SourceConfigError) as ctx:
                open_source_reader(sqlite_conn=conn)
        self.assertEqual(ctx.exception.reason_code, REASON_POSTGRES_CONNECT_FAILED)

    @patch.dict(os.environ, {
        "FUTURES_SOURCE_BACKEND": "sqlite",
    }, clear=True)
    def test_sqlite_source_reads_local_messages(self) -> None:
        with connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE telegram_messages (
                    id INTEGER PRIMARY KEY,
                    message_id TEXT,
                    source TEXT,
                    text TEXT,
                    timestamp INTEGER
                );
                INSERT INTO telegram_messages VALUES (1, '1', 'ch', 'BTC LONG', 1700000000);
            """)
            conn.commit()
            reader = SqliteSourceReader(conn)
            msgs = list(reader.iter_telegram_messages())
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].source, "ch")

    @patch.dict(os.environ, {
        "FUTURES_SOURCE_BACKEND": "postgres",
        "FUTURES_SOURCE_DATABASE_URL": "sqlite:///data/trades.db",
    })
    def test_non_postgres_url_rejected(self) -> None:
        with connect(self.db_path) as conn:
            with self.assertRaises(SourceConfigError) as ctx:
                open_source_reader(sqlite_conn=conn)
        self.assertEqual(ctx.exception.reason_code, REASON_SOURCE_BACKEND_MISMATCH)


class FuturesAuditWithPostgresMockTestCase(unittest.TestCase):
    @patch.dict(os.environ, {
        "FUTURES_SOURCE_BACKEND": "postgres",
        "FUTURES_SOURCE_DATABASE_URL": "postgresql://user:pass@localhost:5432/telegram",
    })
    @patch("bot.research.futures.source_reader._postgres_connect")
    def test_audit_shows_postgres_backend(self, mock_connect: MagicMock) -> None:
        conn_pg = MagicMock()
        mock_connect.return_value = conn_pg

        tmp = tempfile.TemporaryDirectory()
        db_path = Path(tmp.name) / "research.db"
        init_db(db_path)
        with connect(db_path) as research_conn:
            from bot.research.futures.schema import ensure_tables
            ensure_tables(research_conn)
            reader = PostgresSourceReader("postgresql://user:pass@localhost:5432/telegram")
            with patch.object(reader, "list_source_tables", return_value={
                "telegram_messages": {
                    "exists": True, "row_count": 7520, "columns": ["message_id", "text"],
                    "ts_range": (1700000000, 1710000000),
                },
            }), patch.object(reader, "iter_telegram_messages", return_value=iter([])), patch.object(
                reader, "channel_stats", return_value=[],
            ):
                audit = audit_source_data(research_conn, reader)
            reader.close()

        self.assertEqual(audit["source"]["backend"], "postgres")
        self.assertEqual(audit["source"]["status"], "connected")
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
