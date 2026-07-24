"""S65 Strategy Filter Simulator tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import filter_simulator_s65 as s65
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestFilterSimulatorS65(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s65.db")
        self.now = int(time.time())
        self.out = Path(self.tmp.name) / "reports"
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 100) -> None:
        for i in range(n):
            ts = self.now - i * 3600
            direction = "LONG" if i % 2 == 0 else "SHORT"
            if direction == "SHORT":
                pnl = 1.2
                sym = "BTC"
                regime = "WEAK_BEAR"
                strategy = "hist:good"
            else:
                pnl = -0.9
                sym = "ETH"
                regime = "RANGE"
                strategy = "hist:bad"
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, ai_score, hour, weekday, market_regime,
                  created_at, timestamp
                ) VALUES (?, ?, ?, ?, ?, 100, 101, ?, ?, 300, 'TP1',
                  ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1, strategy, i + 1, sym, direction, pnl, pnl,
                    0.001, 0.5, i % 24, i % 7, regime, ts, ts,
                ),
            )
        conn.commit()

    def test_simulate_improves_when_removing_long(self) -> None:
        with research_connection() as conn:
            self._seed(conn)
            out = s65.run_filter_simulator(conn, report_dir=self.out)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("stage"), "S65")
        self.assertLess(out.get("elapsed_sec") or 99, 15)
        self.assertGreater(len(out.get("simulations") or []), 0)
        self.assertGreater(len(out.get("top_filters") or []), 0)

        # Removing losing LONG / ETH / hist:bad should improve PnL
        labels = " ".join(str(f.get("remove_label")) for f in (out.get("simulations") or []))
        self.assertTrue(
            "LONG" in labels or "ETH" in labels or "hist:bad" in labels or "Range" in labels,
            labels,
        )
        best = (out.get("top_filters") or [])[0]
        self.assertGreater(float(best.get("delta_pnl") or 0), 0)
        for key in (
            "trades_removed", "remaining_trades", "old_pnl", "new_pnl", "delta_pnl",
            "old_pf", "new_pf", "old_wr", "new_wr", "old_max_dd", "new_max_dd",
        ):
            self.assertIn(key, best)

        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())
        self.assertIn("TOP FILTERS", md.read_text(encoding="utf-8"))
        payload = json.loads(js.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("stage"), "S65")

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
        self.assertIn("simulate-filters", proc.stdout)


if __name__ == "__main__":
    unittest.main()
