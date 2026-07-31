"""Research Concurrency Fix V1 — lock serialization + multithreaded write stress."""

from __future__ import annotations

import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from bot.research.market_events.db import is_database_locked
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema
from bot.research.market_events.knowledge_engine.store import _record_history
from bot.research.market_events.research_concurrency_v1 import (
    multithreaded_knowledge_history_stress,
)
from bot.research.market_events.research_db_session import (
    knowledge_history_write_guard,
    research_write_connection,
    research_write_lock,
)


class ResearchWriteLockTests(unittest.TestCase):
    def test_nested_lock_same_thread(self) -> None:
        with research_write_lock():
            with research_write_lock():
                self.assertTrue(True)

    def test_lock_serializes_threads(self) -> None:
        order: list[int] = []
        barrier = threading.Barrier(2)

        def worker(n: int) -> None:
            barrier.wait(timeout=5)
            with research_write_lock():
                order.append(n)
                # hold briefly
                import time
                time.sleep(0.05)
                order.append(n + 10)

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        # No interleaving of critical sections: (1,11) then (2,12) or reverse
        self.assertEqual(len(order), 4)
        pairs = [(order[0], order[1]), (order[2], order[3])]
        self.assertIn((1, 11), pairs)
        self.assertIn((2, 12), pairs)


class KnowledgeHistoryConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "research_conc.db"
        configure_unit_test_db_isolation(self.db_path)
        # Point flock path into tmp so tests don't contend with live processes
        self._lock_patch = unittest.mock.patch(
            "bot.research.market_events.research_db_session.RESEARCH_WRITE_LOCK_PATH",
            Path(self._tmpdir.name) / "research_write.lock",
        )
        self._lock_patch.start()

    def tearDown(self) -> None:
        self._lock_patch.stop()
        self._tmpdir.cleanup()

    def test_multithreaded_history_zero_locks(self) -> None:
        out = multithreaded_knowledge_history_stress(
            self.db_path, n_threads=8, n_writes_each=25,
        )
        self.assertEqual(out["n_locked"], 0, msg=out)
        self.assertEqual(out["n_errors"], 0, msg=out)
        self.assertTrue(out["ok"], msg=out)
        with research_write_connection(self.db_path) as conn:
            n = int(conn.execute("SELECT COUNT(*) AS n FROM knowledge_history").fetchone()["n"])
        self.assertGreaterEqual(n, 8 * 25)

    def test_history_guard_blocks_parallel(self) -> None:
        entered = []
        barrier = threading.Barrier(2)

        def worker(tag: str) -> None:
            barrier.wait(timeout=5)
            with knowledge_history_write_guard():
                entered.append(f"{tag}-in")
                import time
                time.sleep(0.03)
                entered.append(f"{tag}-out")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Fully nested pairs, not interleaved in/out across tags
        self.assertEqual(entered[0].endswith("-in"), True)
        self.assertEqual(entered[1], entered[0].replace("-in", "-out"))

    def test_record_history_under_write_connection(self) -> None:
        with research_write_connection(self.db_path) as conn:
            apply_migrations(conn)
            ensure_knowledge_engine_schema(conn)
            _record_history(
                conn,
                entity_type="feature",
                entity_key="rsi",
                field_name="status",
                old_value="candidate",
                new_value="validated",
            )
        with research_write_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT entity_key, new_value FROM knowledge_history LIMIT 1"
            ).fetchone()
        self.assertEqual(row["entity_key"], "rsi")
        self.assertEqual(row["new_value"], "validated")

    def test_is_database_locked_helper(self) -> None:
        import sqlite3
        self.assertTrue(is_database_locked(sqlite3.OperationalError("database is locked")))
        self.assertFalse(is_database_locked(ValueError("nope")))


if __name__ == "__main__":
    unittest.main()
