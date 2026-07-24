"""S64 PnL Killer Analyzer tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import pnl_killers_s64 as s64
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestPnLKillersS64(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s64.db")
        self.now = int(time.time())
        self.out = Path(self.tmp.name) / "reports"
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 120) -> None:
        for i in range(n):
            ts = self.now - i * 3600
            direction = "LONG" if i % 2 == 0 else "SHORT"
            if direction == "SHORT":
                pnl = 1.5 + (i % 3) * 0.1
                sym = "BTC"
                regime = "WEAK_BEAR"
                strategy = "hist:er_v2"
            else:
                pnl = -0.8 - (i % 2) * 0.1
                sym = "ETH"
                regime = "RANGE"
                strategy = "hist:er_v1"
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, ai_score, hour, weekday, market_regime,
                  created_at, timestamp, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, 100, 101, ?, ?, 300, 'TP1',
                  ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1, strategy, i + 1, sym, direction, pnl, pnl,
                    -0.001 if direction == "LONG" else 0.001,
                    0.75 if direction == "SHORT" else 0.35,
                    i % 24, i % 7, regime, ts, ts,
                    json.dumps({"decision_confidence": 0.8 if direction == "SHORT" else 0.3}),
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_decisions_s58 (
                  paper_trade_id, opened_at, symbol, direction, entry_price,
                  rsi, gate_result, gate_reason, why_opened_json,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, 100, ?, 'PASS', ?, ?, ?, ?)
                """,
                (
                    i + 1, ts, sym, direction, 40,
                    "trend_align" if direction == "SHORT" else "mean_revert",
                    json.dumps([{"tag": "funding_ok", "ok": True}]),
                    ts, ts,
                ),
            )
        conn.commit()

    def test_run_exports_and_rankings(self) -> None:
        with research_connection() as conn:
            self._seed(conn)
            out = s64.run_pnl_killers(conn, report_dir=self.out, top_n=10)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("stage"), "S64")
        self.assertLess(out.get("elapsed_sec") or 99, 10)
        self.assertGreater(out.get("n_trades") or 0, 0)
        self.assertIn("strategy", out.get("by_dimension") or {})
        self.assertIn("entry_reason", out.get("by_dimension") or {})
        self.assertGreater(len(out.get("top_profit_generators") or []), 0)
        self.assertGreater(len(out.get("top_loss_generators") or []), 0)

        best = (out.get("top_profit_generators") or [])[0]
        worst = (out.get("top_loss_generators") or [])[0]
        self.assertGreater(float(best.get("net_pnl") or 0), 0)
        self.assertLess(float(worst.get("net_pnl") or 0), 0)
        for key in ("trades", "win_rate", "profit_factor", "net_pnl", "avg_pnl", "max_dd"):
            self.assertIn(key, best)

        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())
        self.assertIn("Top 20 Profit Generators", md.read_text(encoding="utf-8"))
        payload = json.loads(js.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("stage"), "S64")
        self.assertIn("top_loss_generators", payload)

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
        self.assertIn("pnl-killers", proc.stdout)


if __name__ == "__main__":
    unittest.main()
