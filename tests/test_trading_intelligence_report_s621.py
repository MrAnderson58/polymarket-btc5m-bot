"""S62.1 Trading Intelligence Report tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import trading_intelligence_report_s621 as s621
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestTradingIntelligenceReportS621(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s621.db")
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
            ts = self.now - (i % 40) * 3600
            direction = "LONG" if i % 2 == 0 else "SHORT"
            pnl = 8.0 - i * 0.15
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, fear_greed, atr, volume, ai_score, trend, hour, weekday,
                  market_regime, created_at, timestamp
                ) VALUES (?, 's621', ?, ?, ?, 100, 101, ?, ?, 500, 'TP1',
                  ?, 40, 1.0, 1000, ?, 0.2, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1, i + 1,
                    "BTC" if i % 3 else "ETH",
                    direction,
                    pnl, pnl,
                    -0.001 if direction == "LONG" else 0.001,
                    0.3 + (i % 5) * 0.1,
                    i % 24,
                    i % 7,
                    "RANGE" if i % 4 else "WEAK_BULL",
                    ts, ts,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_decisions_s58 (
                  paper_trade_id, opened_at, symbol, direction, entry_price,
                  rsi, gate_result, gate_reason, why_opened_json,
                  final_pnl_usd, exit_reason, closed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 100, ?, 'PASS', 'ALLOWED',
                  ?, ?, 'TP1', ?, ?, ?)
                """,
                (
                    i + 1, ts, "BTC" if i % 3 else "ETH", direction,
                    25 + (i % 50),
                    '[{"tag":"Funding","ok":true},{"tag":"Gate","ok":true}]',
                    pnl, ts, ts, ts,
                ),
            )
        # Cached S59 feature lab run (do not recompute)
        cur = conn.execute(
            """
            INSERT INTO market_events_feature_lab_runs_s59
              (n_trades, results_json, llm_text, llm_method, created_at)
            VALUES (80, '[]', null, 'test', ?)
            """,
            (self.now,),
        )
        run_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO market_events_feature_lab_s59 (
              run_id, feature, filter_rule, on_n, off_n,
              contribution_pf, delta_expectancy, delta_pf, confidence, created_at
            ) VALUES (?, 'Funding', 'Funding favorable', 40, 40, 0.25, 1.2, 0.25, 'HIGH', ?)
            """,
            (run_id, self.now),
        )
        conn.commit()

    def test_report_sections_and_export(self) -> None:
        with research_connection() as conn:
            self._seed(conn, n=80)
        with research_connection(readonly=True) as conn:
            out = s621.run_intelligence_report(
                conn, periods=["lifetime", "24h"], out_dir=self.out, now=self.now,
            )
        self.assertEqual(out.get("n_trades_loaded"), 80)
        self.assertLess(float(out.get("elapsed_sec") or 99), 10.0)
        self.assertIn("lifetime", out.get("by_period") or {})
        life = out["by_period"]["lifetime"]
        self.assertIn("overall", life)
        self.assertIn("best_trades", life)
        self.assertIn("worst_trades", life)
        self.assertIn("coin_ranking", life)
        self.assertIn("direction", life)
        self.assertIn("market_regime", life)
        self.assertIn("hour_analysis", life)
        self.assertIn("weekday_analysis", life)
        self.assertIn("funding_buckets", life)
        self.assertIn("rsi_buckets", life)
        self.assertIn("ai_score_buckets", life)
        self.assertTrue(life.get("feature_importance"))
        self.assertEqual(life["feature_importance"][0]["feature"], "Funding")
        self.assertIn("performance_drift", out)
        paths = out.get("export_paths") or {}
        self.assertTrue(Path(paths["markdown"]).is_file())
        self.assertTrue(Path(paths["json"]).is_file())
        self.assertTrue(Path(paths["ai_context"]).is_file())
        ai = Path(paths["ai_context"]).read_text(encoding="utf-8")
        self.assertIn("AI Context", ai)
        self.assertIn("observe_only=true", ai)
        self.assertLess(len(ai), 12000)

    def test_period_filter(self) -> None:
        rows = [
            {"pnl_usd": 1, "created_at": self.now - 100},
            {"pnl_usd": 2, "created_at": self.now - 100000},
        ]
        recent = s621.filter_by_period(rows, "1h", now=self.now)
        self.assertEqual(len(recent), 1)


if __name__ == "__main__":
    unittest.main()
