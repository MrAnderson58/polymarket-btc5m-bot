"""S59 Feature Laboratory tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import feature_lab_s59 as s59
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestFeatureLabS59(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s59.db")
        self.now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_v69(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 69)
        with research_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE name='market_events_feature_lab_s59'",
            ).fetchone()
            self.assertIsNotNone(row)

    def _seed(self, conn, *, n: int = 80) -> None:
        for i in range(n):
            # Favorable funding LONGs win more often
            direction = "LONG" if i % 2 == 0 else "SHORT"
            funding = -0.001 if direction == "LONG" else 0.001
            # Mix: half flip funding to unfavorable
            if i % 4 == 0:
                funding = -funding
            good_fund = (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0)
            pnl = 12.0 if good_fund else -8.0
            if i % 7 == 0:
                pnl = -pnl * 0.5
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, fear_greed, atr, volume, news_score, macro_score, ai_score,
                  trend, hour, weekday, market_regime, created_at
                ) VALUES ('s59', ?, 'BTC', ?, 100, 101, ?, ?, 300, 'TP1',
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1,
                    direction,
                    pnl,
                    pnl,
                    funding,
                    60 if direction == "LONG" else 40,
                    1.0 + (i % 5) * 0.2,
                    1000 + i * 10,
                    0.7 if good_fund else 0.2,
                    0.6 if good_fund else 0.3,
                    0.8 if good_fund else 0.3,
                    0.5 if direction == "LONG" else -0.5,
                    i % 24,
                    i % 7,
                    "WEAK_BULL" if direction == "LONG" else "WEAK_BEAR",
                    self.now,
                ),
            )
        conn.commit()

    def test_on_off_and_deltas(self) -> None:
        with research_connection() as conn:
            self._seed(conn, n=80)
            out = s59.run_feature_lab(conn, with_llm=False, now=self.now)
            conn.commit()
            self.assertIsNotNone(out.get("run_id"))
            self.assertEqual(out.get("n_trades"), 80)
            feats = {r["feature"]: r for r in out["results"]}
            self.assertIn("Funding", feats)
            self.assertIn("AI", feats)
            self.assertIn("Regime", feats)
            self.assertIn("Hour", feats)
            self.assertIn("Weekday", feats)
            funding = feats["Funding"]
            self.assertGreater(funding["on"]["n"], 0)
            self.assertGreater(funding["off"]["n"], 0)
            self.assertIsNotNone(funding.get("contribution_pf"))
            # Favorable funding should beat unfavorable in this seed
            self.assertGreater(funding["on"]["expectancy"], funding["off"]["expectancy"])

            rows = conn.execute(
                "SELECT * FROM market_events_feature_lab_s59 WHERE run_id = ?",
                (out["run_id"],),
            ).fetchall()
            self.assertEqual(len(rows), 12)

            text = s59.format_feature_lab_report(conn, run_id=out["run_id"])
            self.assertIn("S59 Feature Laboratory", text)
            self.assertIn("Funding", text)
            self.assertIn("Contribution", text)
            self.assertIn("Confidence", text)
            self.assertIn("Useful features", text)

    def test_empty_lab_safe(self) -> None:
        with research_connection() as conn:
            out = s59.run_feature_lab(conn, now=self.now)
            conn.commit()
            self.assertEqual(out.get("n_trades"), 0)
            text = s59.format_feature_lab_report(conn)
            self.assertIn("S59 Feature Laboratory", text)


if __name__ == "__main__":
    unittest.main()
