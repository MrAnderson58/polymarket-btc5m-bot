"""Tests for Research Lake Builder V1."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.signal_intelligence.research_lake_v1.builder import (
    build_research_lake_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.health import (
    research_lake_health_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    load_research_lake_rows,
    research_lake_row_count,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    LAKE_TABLE,
    ensure_research_lake_schema,
)


def _seed(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE market_events_paper_trades_s42 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT, direction TEXT, entry REAL, exit REAL,
          status TEXT, result TEXT, pnl_usd REAL, pnl_pct REAL,
          created_at INTEGER, closed_at INTEGER, updated_at INTEGER,
          gate_decision TEXT
        );
        CREATE TABLE market_events_trade_features_s55 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          paper_trade_id INTEGER,
          s40_signal_type TEXT DEFAULT 't',
          s40_signal_id INTEGER DEFAULT 0,
          symbol TEXT, direction TEXT,
          rsi REAL, atr REAL, funding REAL, oi_delta REAL, fear_greed REAL,
          trend REAL, macro_score REAL, news_score REAL, ai_score REAL,
          market_regime TEXT, gate_decision TEXT, features_json TEXT,
          pnl_pct REAL, pnl_usd REAL, result TEXT,
          created_at INTEGER, closed_at INTEGER
        );
        """
    )
    for i in range(1, 6):
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42
            (symbol, direction, entry, exit, status, result, pnl_usd, pnl_pct,
             created_at, closed_at, updated_at, gate_decision)
            VALUES ('BTC', 'LONG', 100, 101, 'CLOSED', 'WIN', ?, ?,
                    1700000000, 1700001000, 1700001000, 'PASS')
            """,
            (1.0 * i, 0.5 * i),
        )
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55
            (paper_trade_id, symbol, direction, rsi, atr, funding, oi_delta,
             fear_greed, trend, market_regime, gate_decision, features_json,
             pnl_pct, pnl_usd, result, created_at, closed_at)
            VALUES (?, 'BTC', 'LONG', 28, 1.2, -0.0001, 1.5, 40, -0.5,
                    'RISK_ON', 'PASS', '{"rsi":28,"atr_pct":1.1}',
                    ?, ?, 'WIN', 1700000000, 1700001000)
            """,
            (i, 0.5 * i, 1.0 * i),
        )
    conn.commit()


class TestResearchLakeV1(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "lake.db"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.row_factory = sqlite3.Row
        _seed(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_incremental_build_and_loader(self) -> None:
        out = build_research_lake_v1(self.conn, full=False, write_reports=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["rows_inserted"], 5)
        self.assertEqual(research_lake_row_count(self.conn), 5)

        # Incremental again: nothing new
        out2 = build_research_lake_v1(self.conn, full=False, write_reports=False)
        self.assertEqual(out2["rows_inserted"], 0)
        self.assertGreaterEqual(out2["rows_skipped"] + out2["rows_updated"], 0)

        rows = load_research_lake_rows(self.conn)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["_source"], "research_lake_v1")
        self.assertIsNotNone(rows[0].get("pnl"))
        self.assertEqual(rows[0].get("dataset_version"), out["dataset_version"])

        # New trade only
        self.conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42
            (symbol, direction, entry, status, result, pnl_usd, pnl_pct,
             created_at, closed_at, updated_at)
            VALUES ('ETH', 'SHORT', 50, 'CLOSED', 'LOSS', -1, -1,
                    1700002000, 1700003000, 1700003000)
            """
        )
        self.conn.commit()
        out3 = build_research_lake_v1(self.conn, full=False, write_reports=False)
        self.assertEqual(out3["rows_inserted"], 1)
        self.assertEqual(research_lake_row_count(self.conn), 6)

    def test_health_and_schema(self) -> None:
        ensure_research_lake_schema(self.conn)
        build_research_lake_v1(self.conn, full=True, write_reports=False)
        health = research_lake_health_v1(self.conn)
        self.assertEqual(health["n_lake"], 5)
        self.assertEqual(health["duplicates"], 0)
        self.assertIn(health["status"], ("OK", "WARN"))

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m

        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"build-research-lake"', src)
        self.assertIn('"research-lake-health"', src)
        self.assertIn("build_research_lake_v1", src)


if __name__ == "__main__":
    unittest.main()
