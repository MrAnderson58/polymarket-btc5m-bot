"""S64.1 LONG vs SHORT Deep Analyzer tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import long_short_analysis_s641 as s641
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestLongShortAnalysisS641(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s641.db")
        self.now = int(time.time())
        self.out = Path(self.tmp.name) / "reports"
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 80) -> None:
        for i in range(n):
            ts = self.now - i * 3600
            direction = "LONG" if i % 2 == 0 else "SHORT"
            if direction == "SHORT":
                pnl = 1.5
                sym = "BTC"
                regime = "WEAK_BEAR"
                strategy = "hist:good"
            else:
                pnl = -1.0
                sym = "ARB" if i % 4 == 0 else "ETH"
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
                    0.001, 0.5, (i * 3) % 24, i % 7, regime, ts, ts,
                ),
            )
        conn.commit()

    def test_long_loss_roots_and_short_wins(self) -> None:
        with research_connection() as conn:
            self._seed(conn)
            out = s641.run_long_short_analysis(conn, report_dir=self.out)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("stage"), "S64.1")
        self.assertLess(out.get("elapsed_sec") or 99, 10)
        self.assertGreater(out.get("n_long") or 0, 0)
        self.assertGreater(out.get("n_short") or 0, 0)

        long_b = out.get("long") or {}
        self.assertIn("loss_root_causes", long_b)
        roots = long_b.get("loss_root_causes") or []
        self.assertGreater(len(roots), 0)
        sample = roots[0]
        self.assertIn("contribution_to_side_loss_pct", sample)
        self.assertLess(float(sample.get("net_pnl") or 0), 0)

        short_b = out.get("short") or {}
        coin_wins = ((short_b.get("top_wins") or {}).get("coin") or [])
        self.assertGreater(len(coin_wins), 0)
        self.assertGreater(float(coin_wins[0].get("net_pnl") or 0), 0)

        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())
        text = md.read_text(encoding="utf-8")
        self.assertIn("LONG LOSS ROOT CAUSES", text)
        self.assertIn("SHORT SUCCESS FACTORS", text)
        self.assertIn("Contribution to total LONG loss", text)
        payload = json.loads(js.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("stage"), "S64.1")

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
        self.assertIn("long-short-analysis", proc.stdout)


if __name__ == "__main__":
    unittest.main()
