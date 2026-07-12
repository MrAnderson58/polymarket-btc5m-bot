"""Phase G.0/G.0a — PostgreSQL backend, Docker, backup/restore tests."""

from __future__ import annotations

import gzip
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.market_events.config import BASE_DIR
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
from bot.research.market_events.db_tools import (
    BACKUPS_DIR,
    backup_filename,
    db_backup,
    db_benchmark,
    db_check,
    db_restore,
    format_db_info,
    migrate_sqlite_to_postgres,
)
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

    def test_trading_ai_database_from_url(self) -> None:
        os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://trading:secret@localhost:5432/trading_ai"
        cfg = resolve_market_events_db_config()
        self.assertEqual(cfg.backend, "postgresql")
        self.assertEqual(cfg.database_name, "trading_ai")

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

    def test_docker_compose_exists(self) -> None:
        compose = BASE_DIR / "deploy" / "docker" / "docker-compose.yml"
        self.assertTrue(compose.exists())
        text = compose.read_text(encoding="utf-8")
        self.assertIn("trading_ai_db", text)
        self.assertIn("trading_ai", text)
        self.assertIn("postgres:17", text)

    def test_connect_preserves_sqlite_without_wal_on_connect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "del.db"
            conn = sqlite3.connect(str(db))
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.close()
            with market_events_connection(db_path=db) as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(str(mode).lower(), "delete")

    def test_sqlite_migrations_reach_v18(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mig.db"
            with market_events_connection(db_path=db) as conn:
                applied = apply_migrations(conn)
            self.assertIn("v18", applied)
            with market_events_connection(db_path=db) as conn:
                row = conn.execute(
                    "SELECT MAX(version) AS v FROM market_events_migrations",
                ).fetchone()
            self.assertEqual(int(row["v"]), SCHEMA_VERSION)

    def test_postgres_config_never_falls_back_to_sqlite(self) -> None:
        os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://localhost/trading_ai"
        psycopg2, extras = _fake_psycopg2()
        psycopg2.connect.side_effect = OSError("connection refused")
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            with self.assertRaises(MarketEventsDbError):
                with market_events_connection():
                    pass

    def test_postgres_wrapper_detected(self) -> None:
        os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://localhost/trading_ai"
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
            with market_events_connection(db_path=db) as conn:
                apply_migrations(conn)
            text = format_db_info()
            self.assertIn("backend: sqlite", text)
            self.assertIn("schema_version:", text)

    def test_db_check_includes_schema_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "check.db"
            configure_unit_test_db_isolation(db)
            with market_events_connection(db_path=db) as conn:
                apply_migrations(conn)
            text = db_check()
            self.assertIn("tables_ok", text)
            self.assertIn("schema_version:", text)
            self.assertIn("indexes:", text)
            self.assertIn("overall: PASS", text)

    def test_ensure_db_initialized_sqlite_wal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "wal.db"
            configure_unit_test_db_isolation(db)
            mode = ensure_db_initialized()
            self.assertEqual(mode, "wal")

    def test_backup_filename_format(self) -> None:
        name = backup_filename()
        self.assertRegex(name, r"^\d{4}-\d{2}-\d{2}_\d{4}\.sql\.gz$")

    def test_sqlite_backup_creates_gz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "src.db"
            backup_dir = Path(tmp) / "backups"
            configure_unit_test_db_isolation(db)
            with market_events_connection(db_path=db) as conn:
                apply_migrations(conn)
            with patch("bot.research.market_events.db_tools.BACKUPS_DIR", backup_dir):
                dest = backup_dir / backup_filename()
                msg = db_backup(dest=dest)
            self.assertIn("SQLite backup:", msg)
            self.assertTrue(dest.exists())

    def test_migrate_requires_pg_url(self) -> None:
        reset_db_config_for_tests()
        with self.assertRaises(MarketEventsDbError):
            migrate_sqlite_to_postgres()

    def test_migrate_report_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "src.db"
            with market_events_connection(db_path=db) as conn:
                apply_migrations(conn)
            os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://localhost/trading_ai"
            psycopg2, extras = _fake_psycopg2()
            mock_pg = MagicMock()
            psycopg2.connect.return_value = mock_pg
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = {"v": 12, "n": 0, "sid": 0}
            mock_pg.cursor.return_value = mock_cur
            with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
                with patch(
                    "bot.research.market_events.db_tools.market_events_connection",
                ) as mock_conn:
                    src = MagicMock()
                    dst = MagicMock()
                    src.__enter__ = MagicMock(return_value=src)
                    src.__exit__ = MagicMock(return_value=False)
                    dst.__enter__ = MagicMock(return_value=dst)
                    dst.__exit__ = MagicMock(return_value=False)
                    mock_conn.side_effect = [src, dst]
                    src.execute.return_value.fetchone.side_effect = [
                        {"v": 12}, {"n": 0, "sid": 0},
                    ] * 50
                    dst.execute.return_value.fetchone.side_effect = [
                        {"v": 12}, {"n": 0, "sid": 0},
                    ] * 50
                    text = migrate_sqlite_to_postgres(sqlite_path=db, pg_url="postgresql://localhost/trading_ai")
            self.assertIn("trading_ai", text)
            self.assertIn("SQLite source file preserved", text)

    def test_restore_requires_postgres_backend(self) -> None:
        reset_db_config_for_tests()
        with self.assertRaises(MarketEventsDbError):
            db_restore()

    def test_restore_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "2026-07-08_1200.sql.gz"
            with gzip.open(archive, "wb") as gz:
                gz.write(b"-- test dump\n")
            os.environ["MARKET_EVENTS_DB_BACKEND"] = "postgresql"
            os.environ["MARKET_EVENTS_DB_URL"] = "postgresql://trading:pass@localhost:5432/trading_ai"
            with patch("bot.research.market_events.db_tools.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stderr=b"")
                with patch(
                    "bot.research.market_events.db_tools.market_events_connection",
                ) as mock_conn:
                    conn = MagicMock()
                    conn.__enter__ = MagicMock(return_value=conn)
                    conn.__exit__ = MagicMock(return_value=False)
                    conn.execute.return_value.fetchone.return_value = {"v": 12}
                    mock_conn.return_value = conn
                    text = db_restore(archive=archive)
            self.assertIn("SUCCESS", text)

    def test_benchmark_sqlite_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bench.db"
            configure_unit_test_db_isolation(db)
            with market_events_connection(db_path=db) as conn:
                apply_migrations(conn)
            text = db_benchmark(sqlite_path=db, pg_url=None, n=20)
            self.assertIn("sqlite", text)
            self.assertIn("insert_per_sec", text)

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
