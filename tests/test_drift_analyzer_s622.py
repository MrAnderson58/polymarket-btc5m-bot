"""S62.2 Drift Analyzer tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import drift_analyzer_s622 as s622
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestDriftAnalyzerS622(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s622.db")
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
            # Mix: recent 24h losers on SHORT BTC, older lifetime winners
            if i < 30:
                ts = self.now - (i % 20) * 1800  # within ~10h
                pnl = -2.0 - (i % 5) * 0.2
                direction = "SHORT"
                hour = 14
            else:
                ts = self.now - 3 * 86400 - i * 3600
                pnl = 1.5 + (i % 4) * 0.1
                direction = "LONG" if i % 2 == 0 else "SHORT"
                hour = i % 24
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, ai_score, hour, weekday, market_regime,
                  created_at, timestamp
                ) VALUES (?, ?, ?, ?, ?, 100, 101, ?, ?, 400, 'TP1',
                  ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1,
                    "hist:er_v2" if i % 3 else "hist:er_v3",
                    i + 1,
                    "BTC" if i % 2 == 0 else "ETH",
                    direction,
                    pnl,
                    pnl,
                    -0.001 if direction == "LONG" else 0.001,
                    0.55 if i % 2 else 0.25,
                    hour,
                    i % 7,
                    "RANGE" if i % 3 else "WEAK_BULL",
                    ts,
                    ts,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_decisions_s58 (
                  paper_trade_id, opened_at, symbol, direction, entry_price,
                  rsi, gate_result, gate_reason, final_pnl_usd, exit_reason,
                  closed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 100, ?, 'PASS', 'ALLOWED', ?, 'TP1', ?, ?, ?)
                """,
                (
                    i + 1,
                    ts,
                    "BTC" if i % 2 == 0 else "ETH",
                    direction,
                    30 + (i % 40),
                    pnl,
                    ts,
                    ts,
                    ts,
                ),
            )
        # Cached feature lab (existing feature importance)
        cur = conn.execute(
            """
            INSERT INTO market_events_feature_lab_runs_s59 (
              n_trades, results_json, created_at
            ) VALUES (?, '{}', ?)
            """,
            (n, self.now),
        )
        run_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO market_events_feature_lab_s59 (
              run_id, feature, filter_rule, on_n, on_expectancy, on_pf,
              off_n, off_expectancy, off_pf, delta_expectancy, delta_pf,
              contribution_pf, confidence, created_at
            ) VALUES (?, 'Funding', 'LONG funding<0', 40, 1.0, 1.5,
              40, 0.2, 1.0, 0.8, 0.5, 0.3, 'HIGH', ?)
            """,
            (run_id, self.now),
        )
        conn.commit()

    def test_explain_drift_exports_and_ranks(self) -> None:
        with research_connection() as conn:
            self._seed(conn)
            out = s622.run_drift_analyzer(conn, now=self.now, report_dir=self.out)

        self.assertTrue(out.get("ok"))
        self.assertGreater(out.get("n_trades_loaded") or 0, 0)
        self.assertEqual(out.get("as_of_mode"), "wall_clock")
        self.assertIn("what_changed", out)
        self.assertIn("top_deterioration", out)
        self.assertIn("top_improvement", out)
        self.assertLessEqual(len(out.get("top_deterioration") or []), 20)
        self.assertLessEqual(len(out.get("top_improvement") or []), 20)

        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())
        text = md.read_text(encoding="utf-8")
        self.assertIn("What changed?", text)
        self.assertIn("TOP 20 reasons for deterioration", text)
        self.assertIn("Statistics only", text)
        self.assertIn("No recommendations", text)
        self.assertNotIn("## Recommendation", text)

        # SHORT recent window should show up in loss / deterioration somewhere
        dims = {a["dimension"] for a in (out.get("categories") or [])}
        for need in (
            "coin",
            "direction",
            "market_regime",
            "hour",
            "weekday",
            "strategy_version",
            "funding_bucket",
            "rsi_bucket",
            "ai_score_bucket",
            "feature_importance",
        ):
            self.assertIn(need, dims)

        overall = out.get("overall") or {}
        self.assertGreater(int((overall.get("24h") or {}).get("trades") or 0), 0)
        self.assertGreater(int((overall.get("lifetime") or {}).get("trades") or 0), 0)

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
        self.assertIn("explain-drift", proc.stdout)


if __name__ == "__main__":
    unittest.main()
