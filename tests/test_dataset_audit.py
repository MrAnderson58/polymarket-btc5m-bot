"""Tests for S55 dataset-audit CLI."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.expectancy_intelligence.dataset_audit import (
    build_dataset_audit,
    format_dataset_audit,
)


class DatasetAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "audit.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_missing_and_present_features(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  funding, oi_delta, fear_greed, macro_score,
                  features_json, gate_decision, created_at, closed_at, pnl_pct, result
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3", 1, "BTCUSDT", "LONG",
                    0.01, 100.0, None, 0.5,
                    json.dumps({"fear_greed": None}),
                    "ALLOWED", now - 100, now - 50, 1.0, "WIN",
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  funding, fear_greed, features_json, gate_decision,
                  created_at, closed_at, pnl_pct, result
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3", 2, "ETHUSDT", "LONG",
                    0.02, 25.0,
                    json.dumps({"fear_greed": 25.0, "funding": 0.02}),
                    "ALLOWED", now - 90, now - 40, -0.5, "LOSS",
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  funding, fear_greed, features_json, gate_decision,
                  created_at, closed_at, pnl_pct, result
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3", 3, "SOLUSDT", "LONG",
                    0.03, 40.0,
                    json.dumps({"fear_greed": 40.0}),
                    "ALLOWED", now - 80, now - 30, 0.2, "WIN",
                ),
            )
            conn.commit()

            data = build_dataset_audit(conn)
            self.assertEqual(data["n_historical"], 3)
            by_key = {f["key"]: f for f in data["features"]}
            self.assertEqual(by_key["funding"]["missing_pct"], 0.0)
            self.assertGreater(by_key["funding"]["unique"], 1)
            self.assertTrue(by_key["funding"]["correlation_possible"])
            self.assertAlmostEqual(by_key["fear_greed"]["missing_pct"], 33.3, places=1)

            text = format_dataset_audit(conn)
            self.assertIn("DATASET AUDIT", text)
            self.assertIn("Dataset completeness", text)
            self.assertIn("FearGreed", text)


if __name__ == "__main__":
    unittest.main()
