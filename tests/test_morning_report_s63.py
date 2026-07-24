"""S63 Morning Trading Report tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import morning_report_s63 as s63
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestMorningReportS63(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s63.db")
        self.now = int(time.time())
        self.out = Path(self.tmp.name) / "morning"
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 80) -> None:
        for i in range(n):
            ts = self.now - (i % 20) * 1800  # within ~10h
            direction = "LONG" if i % 2 == 0 else "SHORT"
            pnl = 1.0 if direction == "SHORT" else -0.4
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, ai_score, hour, weekday, market_regime,
                  created_at, timestamp
                ) VALUES (?, ?, ?, ?, ?, 100, 101, ?, ?, 200, 'TP1',
                  ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1,
                    "hist:er_v2" if i % 2 else "hist:er_v3",
                    i + 1,
                    "BTC" if i % 3 else "ETH",
                    direction,
                    pnl,
                    pnl,
                    -0.001 if direction == "LONG" else 0.001,
                    0.6,
                    14 + (i % 4),
                    i % 7,
                    "RANGE" if i % 2 else "WEAK_BEAR",
                    ts,
                    ts,
                ),
            )
        conn.commit()

    def test_morning_report_exports(self) -> None:
        with research_connection() as conn:
            self._seed(conn)
            out = s63.run_morning_report(
                conn,
                now=self.now,
                report_dir=self.out,
                root=Path(self.tmp.name),
            )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("stage"), "S63")
        self.assertIn("system", out)
        self.assertIn("trading", out)
        self.assertIn("market", out)
        self.assertIn("patterns", out)
        self.assertIn("ai", out)
        self.assertIn("telegram", out)
        self.assertIn("system_health", out)
        self.assertIn("top_action_items", out)
        self.assertLessEqual(len(out.get("top_action_items") or []), 5)
        self.assertGreater((out.get("trading") or {}).get("trades_window", 0), 0)

        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())
        text = md.read_text(encoding="utf-8")
        for section in (
            "## SYSTEM",
            "## TRADING",
            "## MARKET",
            "## PATTERNS",
            "## AI",
            "## TELEGRAM",
            "## SYSTEM HEALTH",
            "## TOP 5 ACTION ITEMS",
        ):
            self.assertIn(section, text)

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
        self.assertIn("morning-report", proc.stdout)


if __name__ == "__main__":
    unittest.main()
