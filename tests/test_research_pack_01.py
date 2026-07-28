"""Tests for Research Pack 01 trade-statistics."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.research_pack_01.analysis import (
    build_research_pack_01,
    run_trade_statistics,
    write_research_pack_files,
)


class ResearchPack01Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "rp01.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _insert_closed(
        self,
        conn,
        *,
        sid: int,
        pnl: float,
        funding: float,
        trend: float,
        fg: float,
    ) -> None:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55 (
              s40_signal_type, s40_signal_id, symbol, direction,
              funding, trend, fear_greed, oi_delta, volatility, ai_score,
              features_json, gate_decision, created_at, closed_at,
              pnl_pct, result, mfe_pct, mae_pct, duration_sec,
              reached_tp1, stopped, market_regime
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "g3", sid, "BTCUSDT", "LONG",
                funding, trend, fg, 10.0, 1.0, 50.0,
                json.dumps({
                    "funding": funding, "trend": trend, "fear_greed": fg,
                    "decision_confidence": 0.7,
                }),
                "ALLOWED", now - 100, now - 50, pnl,
                "WIN" if pnl > 0 else "LOSS",
                abs(pnl), -abs(pnl) * 0.3, 120,
                1 if pnl > 0 else 0,
                0 if pnl > 0 else 1,
                "RANGE",
            ),
        )

    def test_trade_statistics_and_reports(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            for i, pnl in enumerate([2.0, 1.0, 0.0, -1.5, -2.0]):
                self._insert_closed(
                    conn,
                    sid=i + 1,
                    pnl=pnl,
                    funding=0.01 + i * 0.001,
                    trend=0.2 + i * 0.1,
                    fg=20 + i * 10,
                )
            conn.commit()

            data = build_research_pack_01(conn)
            self.assertEqual(data["trade_statistics"]["total_trades"], 5)
            st = data["trade_statistics"]
            self.assertEqual(
                st["winning_trades"] + st["losing_trades"] + st["breakeven_trades"],
                st["total_trades"],
            )
            self.assertEqual(st["breakeven_trades"], 1)
            self.assertGreater(st["winning_trades"], 0)
            self.assertTrue(data["bucket_analysis"])
            self.assertTrue(data["pair_analysis"])
            self.assertTrue(data["winner_loser"])
            self.assertTrue(data["playbook"]["low_confidence"] or data["playbook"]["profitable"])

            reports = Path(self._tmpdir.name) / "research"
            paths = write_research_pack_files(data, root=reports)
            self.assertTrue(paths["statistics"].exists())
            self.assertTrue(paths["bucket_csv"].exists())
            self.assertTrue(paths["pair_csv"].exists())
            self.assertTrue(paths["playbook"].exists())

            text = run_trade_statistics(conn, write_reports=False)
            self.assertIn("TRADE STATISTICS", text)
            self.assertIn("BUCKET ANALYSIS", text)


if __name__ == "__main__":
    unittest.main()
