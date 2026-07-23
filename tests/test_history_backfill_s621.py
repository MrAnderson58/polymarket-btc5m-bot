"""S62.1 history backfill — research snapshots from trades.db."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.signal_intelligence import history_backfill_s621 as bf
from bot.research.market_events.signal_intelligence import research_repository_s60 as s60
from bot.research.market_events.signal_intelligence.feature_lab_s59 import load_lab_trades


class TestHistoryBackfillS621(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live_path = Path(self.tmp.name) / "live.db"
        self.trades_path = Path(self.tmp.name) / "trades.db"
        configure_unit_test_db_isolation(self.live_path)
        self.repo = s60.get_research_repository()
        self.repo.migrate()
        self._seed_trades_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_trades_db(self) -> None:
        import sqlite3

        conn = sqlite3.connect(self.trades_path)
        now = int(time.time())
        conn.executescript(
            """
            CREATE TABLE early_reversion_v2_trades (
              id INTEGER PRIMARY KEY,
              market_slug TEXT,
              side TEXT,
              strategy_name TEXT,
              status TEXT,
              entry_price REAL,
              entry_ts INTEGER,
              exit_price REAL,
              exit_reason TEXT,
              pnl_percent REAL,
              pnl_usdc REAL,
              holding_time_seconds REAL,
              closed_at TEXT,
              trailing_active INTEGER DEFAULT 0
            );
            CREATE TABLE trade_features (
              id INTEGER PRIMARY KEY,
              trade_id INTEGER,
              source_table TEXT,
              regime_label TEXT,
              mae REAL,
              mfe REAL,
              spread REAL,
              volatility_60s REAL,
              holding_time REAL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO early_reversion_v2_trades (
              id, market_slug, side, strategy_name, status, entry_price, entry_ts,
              exit_price, exit_reason, pnl_percent, pnl_usdc, holding_time_seconds,
              closed_at, trailing_active
            ) VALUES
              (1, 'btc-updown-5m-1', 'YES', 'er_v2', 'closed', 0.45, ?,
               0.55, 'TP', 10.0, 0.10, 45.0, datetime('now'), 0),
              (2, 'btc-updown-5m-2', 'NO', 'er_v2', 'closed', 0.40, ?,
               0.30, 'STOP', -8.0, -0.08, 30.0, datetime('now'), 1),
              (3, 'btc-updown-5m-3', 'YES', 'er_v2', 'open', 0.50, ?,
               NULL, NULL, NULL, NULL, NULL, NULL, 0)
            """,
            (now - 100, now - 80, now - 10),
        )
        conn.execute(
            """
            INSERT INTO trade_features (
              trade_id, source_table, regime_label, mae, mfe, spread, volatility_60s, holding_time
            ) VALUES (1, 'early_reversion_v2_trades', 'RANGE', -0.02, 0.12, 0.01, 0.003, 45)
            """
        )
        conn.commit()
        conn.close()

    def test_discover_and_backfill_idempotent(self) -> None:
        sources = bf.discover_trade_sources(
            trades_db=self.trades_path,
            live_db=self.live_path,
        )
        er = next(s for s in sources if s["table"] == "early_reversion_v2_trades")
        self.assertEqual(er["completed"], 2)

        with s60.research_connection() as conn:
            r1 = bf.run_history_backfill(
                conn, trades_db=self.trades_path, live_db=self.live_path,
            )
            self.assertEqual(r1["trades_found"], 2)
            self.assertEqual(r1["trades_imported"], 2)
            self.assertEqual(r1["verify_count"], 2)

            r2 = bf.run_history_backfill(
                conn, trades_db=self.trades_path, live_db=self.live_path,
            )
            self.assertEqual(r2["trades_imported"], 0)
            self.assertEqual(r2["trades_skipped"], 2)
            self.assertEqual(r2["verify_count"], 2)

            rows = load_lab_trades(conn)
            self.assertEqual(len(rows), 2)
            symbols = {r["symbol"] for r in rows}
            self.assertEqual(symbols, {"BTC"})
            dirs = {r["direction"] for r in rows}
            self.assertEqual(dirs, {"LONG", "SHORT"})

    def test_cli_registered(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("backfill-history", proc.stdout)


if __name__ == "__main__":
    unittest.main()
