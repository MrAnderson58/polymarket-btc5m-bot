"""Phase G.0 — PostgreSQL backend tests for market_events."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.market_events.db import (
    MarketEventsDbError,
    PostgresBackend,
    connection_is_postgres,
    ensure_db_initialized,
    insert_returning_id,
    market_events_connection,
)
from bot.research.market_events.db_config import (
    configure_unit_test_db_isolation,
    reset_db_config_for_tests,
    resolve_market_events_db_config,
)
from bot.research.market_events.db_tools import db_check, format_db_info
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations


def _fake_psycopg2():
    extras = MagicMock()
    extras.RealDictCursor = MagicMock()
    psycopg2 = MagicMock()
    psycopg2.extras = extras
    return psycopg2, extras


class MarketEventsPostgresConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_db_config_for_tests()

    def test_default_backend_is_sqlite(self) -> None:
        reset_db_config_for_tests()
        os.environ.pop("MARKET_EVENTS_DB_BACKEND", None)
        os.environ.pop("MARKET_EVENTS_DB_URL", None)
        cfg = resolve_market_events_db_config()
        self.assertEqual(cfg.backend, "sqlite")
        self.assertFalse(cfg.postgres_url_configured)

    def test_postgres_backend_from_url(self) -> None:
        os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://localhost/market_events"
        cfg = resolve_market_events_db_config()
        self.assertEqual(cfg.backend, "postgresql")
        self.assertTrue(cfg.postgres_url_configured)

    def test_postgres_backend_requires_url_when_backend_set(self) -> None:
        os.environ["MARKET_EVENTS_DB_BACKEND"] = "postgresql"
        os.environ.pop("MARKET_EVENTS_DB_URL", None)
        with self.assertRaises(RuntimeError):
            resolve_market_events_db_config()

    def test_unit_test_isolation_forces_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "unit.db"
            configure_unit_test_db_isolation(db)
            cfg = resolve_market_events_db_config()
            self.assertEqual(cfg.backend, "sqlite")
            self.assertEqual(cfg.sqlite_path, db)

    def test_connect_preserves_sqlite_without_wal_on_connect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "del.db"
            conn = sqlite3.connect(str(db))
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.close()
            with market_events_connection(db_path=db) as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(str(mode).lower(), "delete")

    def test_sqlite_migrations_reach_v12(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mig.db"
            with market_events_connection(db_path=db) as conn:
                applied = apply_migrations(conn)
            self.assertIn("v12", applied)
            with market_events_connection(db_path=db) as conn:
                row = conn.execute(
                    "SELECT MAX(version) AS v FROM market_events_migrations",
                ).fetchone()
            self.assertEqual(int(row["v"]), SCHEMA_VERSION)

    def test_postgres_config_never_falls_back_to_sqlite(self) -> None:
        os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://localhost/market_events"
        psycopg2, extras = _fake_psycopg2()
        psycopg2.connect.side_effect = OSError("connection refused")
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            with self.assertRaises(MarketEventsDbError):
                with market_events_connection():
                    pass

    def test_postgres_wrapper_detected(self) -> None:
        os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://localhost/market_events"
        psycopg2, extras = _fake_psycopg2()
        mock_pg = MagicMock()
        psycopg2.connect.return_value = mock_pg
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            with market_events_connection() as conn:
                self.assertTrue(connection_is_postgres(conn))
                self.assertIsInstance(conn, PostgresBackend)

    def test_format_db_info_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "info.db"
            configure_unit_test_db_isolation(db)
            text = format_db_info()
            self.assertIn("backend: sqlite", text)

    def test_db_check_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "check.db"
            configure_unit_test_db_isolation(db)
            text = db_check()
            self.assertIn("tables_ok", text)

    def test_ensure_db_initialized_sqlite_wal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "wal.db"
            configure_unit_test_db_isolation(db)
            mode = ensure_db_initialized()
            self.assertEqual(mode, "wal")

    def test_insert_returning_id_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ins.db"
            with market_events_connection(db_path=db) as conn:
                apply_migrations(conn)
                eid = insert_returning_id(
                    conn,
                    """
                    INSERT INTO market_events (
                      event_ts, detected_ts, venue, symbol, direction, phase,
                      trigger_window_seconds, return_pct, classification,
                      detector_version, detector_triggers_json, dedup_key, created_at
                    ) VALUES (?, ?, 'binance_futures', 'BTC', 'DOWN', 'SHOCK_DETECTED',
                      60, -2.0, 'ASSET_SPECIFIC', 'v1', '[]', 'dedup-pg-test', ?)
                    """,
                    (1, 1, 1),
                )
            self.assertGreater(eid, 0)


if __name__ == "__main__":
    unittest.main()
