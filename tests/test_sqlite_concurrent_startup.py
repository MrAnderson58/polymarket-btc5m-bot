"""Phase E.5.3.2 — SQLite concurrent writer hardening regression tests."""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import (
    execute_with_retry,
    insert_returning_id,
    is_database_locked,
    lock_retry_sleep_schedule,
    market_events_connection,
    retry_on_db_locked,
    with_retry_transaction,
)
from bot.research.market_events.event_schema import apply_migrations


def _startup_worker(db_path: str, mode: str, lock_path: str, out_queue: multiprocessing.Queue) -> None:
    """Simulate collector startup: lock → migrate → universe INSERT."""
    os.environ["MARKET_EVENTS_DATABASE_PATH"] = db_path
    try:
        import bot.research.market_events.startup_lock as sl
        import bot.research.market_events.config as cfg

        sl.STARTUP_LOCK_PATH = Path(lock_path)
        cfg.MARKET_EVENTS_DATABASE_PATH = Path(db_path)

        from bot.research.market_events.db import market_events_connection
        from bot.research.market_events.event_schema import apply_migrations
        from bot.research.market_events.observe_scope import select_observe_universe
        from bot.research.market_events.startup_lock import market_events_startup_lock
        from bot.research.market_events.universe import select_universe

        with market_events_startup_lock():
            with market_events_connection(db_path=Path(db_path)) as conn:
                apply_migrations(conn)
                if mode == "observe":
                    select_observe_universe(conn, mode="tradfi-observe")
                else:
                    select_universe(conn, mode=mode)
        out_queue.put(("ok", mode))
    except Exception as exc:
        out_queue.put(("error", f"{mode}: {exc}"))


class SqliteConcurrentStartupTests(unittest.TestCase):
    def test_pragmas_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "pragma.db"
            with market_events_connection(db_path=db) as conn:
                journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
                sync = conn.execute("PRAGMA synchronous").fetchone()[0]
                timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(journal.lower(), "wal")
            self.assertEqual(int(sync), 1)  # NORMAL
            self.assertGreaterEqual(int(timeout), 10000)
            self.assertEqual(int(fk), 1)

    def test_lock_retry_schedule_caps_at_10s(self) -> None:
        schedule = lock_retry_sleep_schedule()
        self.assertGreaterEqual(len(schedule), 5)
        self.assertAlmostEqual(sum(schedule), 10.0, delta=0.01)
        self.assertEqual(schedule[0], 0.05)

    def test_retry_on_db_locked_eventually_succeeds(self) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        with patch("bot.research.market_events.db.time.sleep"):
            result = retry_on_db_locked(flaky)
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)

    def test_concurrent_inserts_no_lock_errors(self) -> None:
        import json
        import time

        errors: list[str] = []
        barrier = threading.Barrier(5)

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "shared.db"

            def insert_worker(i: int) -> None:
                try:
                    barrier.wait(timeout=10)
                    with market_events_connection(db_path=db) as conn:
                        apply_migrations(conn)
                        insert_returning_id(
                            conn,
                            """
                            INSERT INTO market_events_universe_log (
                              version_tag, symbols_json, selection_reason, created_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (f"t-{i}", json.dumps(["BTC"]), f"worker={i}", int(time.time())),
                        )
                except Exception as exc:
                    if "locked" in str(exc).lower():
                        errors.append(str(exc))

            with market_events_connection(db_path=db) as conn:
                apply_migrations(conn)

            threads = [threading.Thread(target=insert_worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            self.assertEqual(errors, [], msg=f"lock errors: {errors}")
            with market_events_connection(db_path=db) as conn:
                n = conn.execute("SELECT COUNT(*) FROM market_events_universe_log").fetchone()[0]
                self.assertEqual(int(n), 5)

    def test_with_retry_transaction_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tx.db"
            with market_events_connection(db_path=db) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS _tx (id INTEGER PRIMARY KEY, v TEXT)")
            with market_events_connection(db_path=db) as conn:
                with with_retry_transaction(conn):
                    execute_with_retry(conn, "INSERT INTO _tx (v) VALUES (?)", ("a",))
            with market_events_connection(db_path=db) as conn:
                n = conn.execute("SELECT COUNT(*) FROM _tx").fetchone()[0]
                self.assertEqual(n, 1)

    def test_concurrent_startup_core_tradfi_observe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "concurrent.db")
            lock_path = str(Path(tmp) / "startup.lock")
            queue: multiprocessing.Queue = multiprocessing.Queue()
            modes = ("core", "tradfi-liquid", "observe")
            procs = [
                multiprocessing.Process(
                    target=_startup_worker,
                    args=(db_path, mode, lock_path, queue),
                )
                for mode in modes
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=60)
                self.assertEqual(p.exitcode, 0)

            results = [queue.get(timeout=5) for _ in modes]
            errors = [msg for status, msg in results if status == "error"]
            self.assertEqual(errors, [], msg=f"startup errors: {errors}")

            with market_events_connection(db_path=Path(db_path)) as conn:
                n = conn.execute(
                    "SELECT COUNT(*) FROM market_events_universe_log",
                ).fetchone()[0]
                self.assertEqual(int(n), 3)

    def test_is_database_locked_recognizes_message(self) -> None:
        exc = sqlite3.OperationalError("database is locked")
        self.assertTrue(is_database_locked(exc))
        self.assertFalse(is_database_locked(sqlite3.OperationalError("no such table")))


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    unittest.main()
