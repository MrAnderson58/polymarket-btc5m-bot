"""Tests for Research Infrastructure Finalization V1."""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from bot.research.market_events.research_write_manager import (
    research_replace_table,
    research_write_batch,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.fixes import (
    fix_s55_audit,
    fix_s55_infrastructure,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    DATASET_VERSION,
    LAKE_TABLE,
    ensure_research_lake_schema,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _ensure_s55(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_events_trade_features_s55 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_trade_id INTEGER,
            s40_signal_type TEXT NOT NULL,
            s40_signal_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            features_json TEXT,
            created_at INTEGER NOT NULL DEFAULT 0,
            closed_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS market_events_paper_trades_s42 (
            id INTEGER PRIMARY KEY,
            s40_signal_type TEXT,
            s40_signal_id INTEGER,
            symbol TEXT,
            direction TEXT,
            status TEXT,
            pnl_pct REAL,
            created_at INTEGER,
            closed_at INTEGER
        );
        """
    )
    conn.commit()


class TestWriteManager(unittest.TestCase):
    def test_replace_table(self):
        conn = _conn()
        conn.execute("CREATE TABLE t(k TEXT PRIMARY KEY, v INTEGER)")
        research_replace_table(
            conn,
            delete_sql="DELETE FROM t",
            insert_sql="INSERT INTO t(k,v) VALUES (?,?)",
            rows=[("a", 1), ("b", 2)],
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 2)

    def test_write_batch_commit(self):
        conn = _conn()
        conn.execute("CREATE TABLE t(v INTEGER)")

        def _fn(c):
            c.execute("INSERT INTO t(v) VALUES (1)")
            return 1

        self.assertEqual(research_write_batch(conn, _fn), 1)


class TestFixS55Crash(unittest.TestCase):
    def test_audit_no_int_in_check(self):
        conn = _conn()
        ensure_research_lake_schema(conn)
        _ensure_s55(conn)
        conn.execute(
            f"""
            INSERT INTO {LAKE_TABLE} (
                trade_id, symbol, pnl, opened_at, closed_at,
                feature_version, dataset_version, schema_version, updated_at, built_at
            ) VALUES (1,'BTC',1.0,100,200,'v1','{DATASET_VERSION}','1.0.0',1,1)
            """
        )
        conn.commit()
        out = fix_s55_audit(conn)
        self.assertIn("unexpected_s55", out)
        self.assertIsInstance(out["ok"], bool)

    def test_infrastructure_dry_run(self):
        conn = _conn()
        ensure_research_lake_schema(conn)
        _ensure_s55(conn)
        out = fix_s55_infrastructure(conn, apply=False)
        self.assertIn("unexpected_s55", out)
        self.assertEqual(out.get("impossible_explanation"), [])


class TestInfraSelftest(unittest.TestCase):
    def test_format(self):
        from bot.research.market_events.signal_intelligence.research_infrastructure_v1.selftest import (
            InfraCheck,
            InfraSelfTestReport,
            format_infrastructure_selftest,
        )

        report = InfraSelfTestReport(
            checks=[InfraCheck(name="Integrity", passed=True, detail="ok")],
            elapsed_sec=1.0,
        )
        text = format_infrastructure_selftest(report)
        self.assertIn("Integrity", text)
        self.assertIn("PASS", text)


if __name__ == "__main__":
    unittest.main()
