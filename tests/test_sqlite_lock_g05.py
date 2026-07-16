"""Phase G.0.5 — SQLite lock root-cause elimination tests."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import (
    market_events_connection,
    market_events_readonly_connection,
)
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.sqlite_lock_smoke_g05 import (
    run_sqlite_lock_smoke_g05,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    handle_market_events_command,
)
from bot.research.market_events.sqlite_manager_g05 import (
    PURE_READONLY_COMMANDS,
    connect_sqlite,
    format_sqlite_lock_debug_g05,
    get_active_leases,
)


class SqliteLockG05Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g05.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_connect_logs_registry(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            leases = get_active_leases()
            self.assertTrue(any(not l.readonly for l in leases))
        self.assertEqual(len(get_active_leases()), 0)

    def test_readonly_forbids_commit(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        conn = connect_sqlite(self.db_path, readonly=True)
        try:
            with self.assertRaises(Exception):
                conn.commit()
        finally:
            conn.close()

    def test_pure_ro_commands_no_write_trace(self) -> None:
        for cmd in (
            "/status", "/market", "/health", "/top", "/help",
            "/decision", "/explain-decision", "/pattern", "/news",
            "/review", "/paper", "/learning-status",
        ):
            self.assertIn(cmd, PURE_READONLY_COMMANDS, msg=cmd)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

        writes: list[str] = []

        def _spy_connect(path, *, readonly=False, **kwargs):
            if not readonly:
                writes.append("write")
            return connect_sqlite(path, readonly=readonly, **kwargs)

        with patch(
            "bot.research.market_events.db.connect_sqlite",
            side_effect=_spy_connect,
        ):
            for cmd in ("/status", "/market", "/health", "/top"):
                writes.clear()
                result = handle_market_events_command(cmd)
                self.assertTrue(result.ok, msg=result.reply_text)
                self.assertEqual(writes, [], msg=f"{cmd} opened write conn")

    def test_lock_debug_format(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            text = format_sqlite_lock_debug_g05()
            self.assertIn("Current writer", text)
            self.assertIn("Waiting readers", text)

    def test_smoke_zero_locked(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        stats = run_sqlite_lock_smoke_g05(per_command=20, writer_threads=2)
        self.assertEqual(stats["locked"], 0, msg=stats)
        self.assertTrue(stats["pass"], msg=stats)

    def test_concurrent_readonly_under_writer(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

        stop = threading.Event()
        errors: list[str] = []

        def writer() -> None:
            while not stop.is_set():
                try:
                    with market_events_connection() as conn:
                        conn.execute(
                            """
                            INSERT INTO market_events_command_trace_g351 (
                              message_id, command, stage, status, created_at
                            ) VALUES (NULL, '/w', 'T', 'PASS', 1)
                            """,
                        )
                except Exception as exc:
                    errors.append(str(exc))
                stop.wait(0.01)

        def reader() -> None:
            for _ in range(50):
                try:
                    with market_events_readonly_connection() as conn:
                        conn.execute("SELECT COUNT(*) FROM market_events_command_trace_g351")
                except Exception as exc:
                    errors.append(str(exc))

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        reader()
        stop.set()
        t.join(timeout=2)
        locked = [e for e in errors if "database is locked" in e.lower()]
        self.assertEqual(locked, [])


if __name__ == "__main__":
    unittest.main()
