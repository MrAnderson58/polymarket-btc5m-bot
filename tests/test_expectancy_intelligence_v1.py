"""Tests for Expectancy Intelligence V1 CLIs."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.expectancy_intelligence.breakdown import (
    build_expectancy_breakdown,
    format_expectancy_breakdown,
)
from bot.research.market_events.expectancy_intelligence.counterfactual import (
    format_counterfactual,
)
from bot.research.market_events.expectancy_intelligence.daily_report import (
    build_daily_intelligence,
    write_daily_intelligence_report,
)
from bot.research.market_events.expectancy_intelligence.feature_importance import (
    format_feature_importance,
)
from bot.research.market_events.expectancy_intelligence.knowledge_record import (
    record_paper_trade_knowledge,
)
from bot.research.market_events.expectancy_intelligence.similar_explorer import (
    format_similar_trades,
)
from bot.research.market_events.expectancy_intelligence.stats import pearson
from bot.research.market_events.trade_intelligence.schema import ensure_trade_intelligence_schema


class ExpectancyIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "ei.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_pearson(self) -> None:
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertAlmostEqual(pearson(xs, xs) or 0, 1.0, places=3)

    def _seed_closed_neighbor(self, conn, now: int, pnl: float, sym: str = "BTCUSDT") -> None:
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55 (
              s40_signal_type, s40_signal_id, symbol, direction,
              fear_greed, trend, funding, volatility, features_json,
              gate_decision, gate_expected_pnl_pct, similar_count,
              created_at, closed_at, pnl_pct, result, mfe_pct, mae_pct,
              reached_tp1, stopped
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "g3", 100 + int(pnl), sym, "LONG",
                30.0, 0.5, 0.01, 1.2,
                json.dumps({"fear_greed": 30.0, "trend": 0.5}),
                "ALLOWED", pnl, 25, now - 100, now - 50, pnl,
                "WIN" if pnl > 0 else "LOSS",
                abs(pnl) + 0.5, -abs(pnl) * 0.5,
                1 if pnl > 0 else 0,
                0 if pnl > 0 else 1,
            ),
        )

    def test_breakdown_and_counterfactual(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            for i, pnl in enumerate([2.0, -1.0, 1.5, -2.0, 0.5]):
                self._seed_closed_neighbor(conn, now, pnl)
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  fear_greed, trend, features_json, gate_decision,
                  gate_expected_pnl_pct, similar_count, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3", 1, "BTCUSDT", "LONG", 30.0, 0.5,
                    json.dumps({"fear_greed": 30.0, "trend": 0.5}),
                    "NEGATIVE_EXPECTANCY", -1.2, 25, now,
                ),
            )
            conn.commit()

            bd = build_expectancy_breakdown(conn, since_hours=1)
            self.assertEqual(bd["n_rejected"], 1)
            text = format_expectancy_breakdown(conn, since_hours=1)
            self.assertIn("NEGATIVE EXPECTANCY", text)
            self.assertIn("BTCUSDT", text)

            cf = format_counterfactual(conn, since_hours=1)
            self.assertIn("COUNTERFACTUAL", cf)
            self.assertIn("virtual_pnl", cf)

    def test_feature_importance_and_similar(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_closed_neighbor(conn, now, 1.0)
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  fear_greed, trend, features_json, gate_decision,
                  gate_expected_pnl_pct, similar_count, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3", 2, "BTCUSDT", "LONG", 30.0, 0.5,
                    json.dumps({"fear_greed": 30.0}),
                    "NEGATIVE_EXPECTANCY", -0.5, 10, now,
                ),
            )
            conn.commit()
            fi = format_feature_importance(conn)
            self.assertIn("FEATURE IMPORTANCE", fi)
            sim = format_similar_trades(conn, symbol="BTC")
            self.assertIn("SIMILAR TRADES", sim)

    def test_daily_and_knowledge(self) -> None:
        now = int(time.time())
        reports = Path(self._tmpdir.name) / "reports"
        with market_events_connection() as conn:
            apply_migrations(conn)
            ensure_trade_intelligence_schema(conn)
            self._seed_closed_neighbor(conn, now, 1.0)
            conn.commit()
            data = build_daily_intelligence(conn)
            self.assertGreaterEqual(data["observed"], 0)
            path = write_daily_intelligence_report(
                conn, reports_root=reports / "daily",
            )
            self.assertTrue(path.exists())

            record_paper_trade_knowledge(
                conn,
                paper_trade_id=42,
                row={
                    "id": 42,
                    "symbol": "BTCUSDT",
                    "direction": "LONG",
                    "entry": 100.0,
                    "created_at": now - 60,
                    "s40_signal_type": "g3",
                    "s40_signal_id": 1,
                },
                pnl_pct=1.0,
                pnl_usd=5.0,
                mfe_pct=1.5,
                mae_pct=-0.5,
                exit_reason="TP1",
                result="WIN",
                duration_sec=60,
            )
            conn.commit()
            row = conn.execute(
                "SELECT knowledge_json FROM ti_paper_knowledge WHERE paper_trade_id = 42"
            ).fetchone()
            self.assertIsNotNone(row)
            blob = json.loads(row["knowledge_json"])
            self.assertIn("lessons", blob)
            self.assertIn("features", blob)


if __name__ == "__main__":
    unittest.main()
