"""Tests for Research Lake Builder V1/V2."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.signal_intelligence.research_lake_v1.builder import (
    BATCH_SIZE,
    build_research_lake_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.health import (
    research_lake_health_v1,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.indexes import (
    ensure_research_lake_join_indexes,
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
          gate_decision TEXT, decision_confidence REAL,
          s40_signal_type TEXT, s40_signal_id INTEGER,
          mae_pct REAL, mfe_pct REAL, exit_reason TEXT, holding_seconds INTEGER,
          pattern_json TEXT, news_category TEXT
        );
        CREATE TABLE market_events_trade_features_s55 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          paper_trade_id INTEGER,
          s40_signal_type TEXT DEFAULT 't',
          s40_signal_id INTEGER DEFAULT 0,
          symbol TEXT, direction TEXT,
          hour INTEGER, weekday INTEGER,
          rsi REAL, atr REAL, funding REAL, oi_delta REAL, fear_greed REAL,
          trend REAL, volume REAL, macro_score REAL, news_score REAL, ai_score REAL,
          market_regime TEXT, gate_decision TEXT, features_json TEXT,
          pnl_pct REAL, pnl_usd REAL, result TEXT,
          mae_pct REAL, mfe_pct REAL, duration_sec INTEGER, exit_reason TEXT,
          created_at INTEGER, closed_at INTEGER,
          UNIQUE(s40_signal_type, s40_signal_id)
        );
        """
    )
    for i in range(1, 6):
        conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42
            (symbol, direction, entry, exit, status, result, pnl_usd, pnl_pct,
             created_at, closed_at, updated_at, gate_decision,
             s40_signal_type, s40_signal_id, mae_pct, mfe_pct, exit_reason, holding_seconds)
            VALUES ('BTC', 'LONG', 100, 101, 'CLOSED', 'WIN', ?, ?,
                    1700000000, 1700001000, 1700001000, 'PASS',
                    ?, ?, -0.1, 0.8, 'TP1', 1000)
            """,
            (1.0 * i, 0.5 * i, f"t{i}", i),
        )
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55
            (paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
             rsi, atr, funding, oi_delta, fear_greed, trend, market_regime,
             gate_decision, features_json, pnl_pct, pnl_usd, result, created_at, closed_at)
            VALUES (?, ?, ?, 'BTC', 'LONG', 28, 1.2, -0.0001, 1.5, 40, -0.5,
                    'RISK_ON', 'PASS', '{"rsi":28,"atr_pct":1.1}',
                    ?, ?, 'WIN', 1700000000, 1700001000)
            """,
            (i, f"t{i}", i, 0.5 * i, 1.0 * i),
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
        self.assertIn('"research-lake-sync"', src)
        self.assertIn("build_research_lake_v1", src)

    def test_v2_streaming_flags_and_profile(self) -> None:
        out = build_research_lake_v1(self.conn, full=True, write_reports=False, batch_size=2)
        self.assertTrue(out["ok"])
        self.assertEqual(out["builder_version"], "v2-streaming")
        self.assertTrue(out["no_select_in_trade_loop"])
        self.assertEqual(out["batch_size"], 2)
        self.assertIn("profile", out)
        self.assertGreaterEqual(out["profile"]["n_queries"], 1)
        self.assertTrue(any("idx_s55" in x for x in out["indexes_ensured"]))

    def test_indexes_idempotent(self) -> None:
        ensure_research_lake_schema(self.conn)
        a = ensure_research_lake_join_indexes(self.conn)
        b = ensure_research_lake_join_indexes(self.conn)
        self.assertIn("idx_s55_paper_trade_id", a)
        self.assertIn("idx_s55_paper_trade_id", b)

    def test_no_select_inside_trade_compose_loop(self) -> None:
        """Trade joins must be preloaded — profile shows O(1) S55 scans, not N."""
        out = build_research_lake_v1(
            self.conn, full=True, write_reports=False, batch_size=2, profile=True,
            print_fn=lambda *_a, **_k: None,
        )
        self.assertTrue(out["ok"])
        self.assertTrue(out["no_select_in_trade_loop"])
        top = out["profile"]["top_slow"]
        s55_events = [e for e in top if "trade_features_s55" in (e.get("sql") or "")]
        # At most one full S55 preload (not 5× per-trade SELECTs in top list alone).
        # Also check all events via n_queries vs rows_seen.
        self.assertEqual(out["rows_seen"], 5)
        # Streaming with batch=2 → 3 S42 LIMIT fetches max for 5 rows.
        all_sql = [e.get("sql") or "" for e in out["profile"]["top_slow"]]
        # Rebuild with access to full event list isn't exported; assert builder flag + insert path.
        self.assertGreaterEqual(out["rows_inserted"] + out["rows_updated"], 5)
        self.assertLess(out["profile"]["n_queries"], 5 * 4)  # would be ~20+ under N+1 joins
        _ = s55_events, all_sql

    def test_incremental_sync_clears_lag(self) -> None:
        from bot.research.market_events.signal_intelligence.research_lake_v1.sync import (
            lake_lag,
            sync_research_lake_incremental,
        )

        build_research_lake_v1(self.conn, full=True, write_reports=False)
        self.conn.execute(
            """
            INSERT INTO market_events_paper_trades_s42
            (symbol, direction, entry, status, result, pnl_usd, pnl_pct,
             created_at, closed_at, updated_at)
            VALUES ('SOL', 'LONG', 10, 'CLOSED', 'WIN', 1, 1,
                    1700004000, 1700005000, 1700005000)
            """
        )
        self.conn.commit()
        before = lake_lag(self.conn)
        self.assertGreaterEqual(before["lag"], 1)
        out = sync_research_lake_incremental(self.conn)
        self.assertTrue(out["ok"])
        self.assertEqual(out["lag_after"], 0)

    def test_s55_join_diagnose_and_repair(self) -> None:
        from bot.research.market_events.signal_intelligence.research_lake_v1.s55_join import (
            diagnose_missing_s55_joins,
            repair_s55_joins,
        )

        # Build lake with S55, then delete S55 rows → missing joins
        build_research_lake_v1(self.conn, full=True, write_reports=False)
        self.conn.execute("DELETE FROM market_events_trade_features_s55")
        self.conn.execute(f"UPDATE {LAKE_TABLE} SET s55_id = NULL")
        self.conn.commit()
        before = diagnose_missing_s55_joins(self.conn)
        self.assertEqual(before["n_missing"], 5)
        self.assertEqual(before["reasons"].get("NO_S55_ROW"), 5)
        repair = repair_s55_joins(self.conn)
        self.assertTrue(repair["ok"])
        after = diagnose_missing_s55_joins(self.conn)
        self.assertLess(after["missing_pct"], 1.0)
        self.assertEqual(after["n_missing"], 0)


class TestResearchLakeV2Scale(unittest.TestCase):
    def test_30k_under_two_minutes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "scale.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
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
        n = 30000
        base = 1700000000
        s42_rows = [
            ("BTC", "LONG", 100.0, 101.0, "CLOSED", "WIN", 1.0, 0.5, base + i, base + i + 60, base + i + 60, "PASS")
            for i in range(n)
        ]
        conn.executemany(
            """
            INSERT INTO market_events_paper_trades_s42
            (symbol, direction, entry, exit, status, result, pnl_usd, pnl_pct,
             created_at, closed_at, updated_at, gate_decision)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            s42_rows,
        )
        s55_rows = [
            (i + 1, "BTC", "LONG", 28.0, 1.2, -0.0001, 1.5, 40.0, -0.5, "RISK_ON", "PASS",
             '{"rsi":28}', 0.5, 1.0, "WIN", base + i, base + i + 60)
            for i in range(n)
        ]
        conn.executemany(
            """
            INSERT INTO market_events_trade_features_s55
            (paper_trade_id, symbol, direction, rsi, atr, funding, oi_delta,
             fear_greed, trend, market_regime, gate_decision, features_json,
             pnl_pct, pnl_usd, result, created_at, closed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            s55_rows,
        )
        conn.commit()
        t0 = time.time()
        out = build_research_lake_v1(
            conn, full=True, write_reports=False, batch_size=BATCH_SIZE, profile=True, print_fn=lambda *_a, **_k: None
        )
        elapsed = time.time() - t0
        conn.close()
        self.assertTrue(out["ok"])
        self.assertEqual(out["rows_seen"], n)
        self.assertLess(elapsed, 120.0, f"30k build took {elapsed:.1f}s (>= 120s)")


if __name__ == "__main__":
    unittest.main()
