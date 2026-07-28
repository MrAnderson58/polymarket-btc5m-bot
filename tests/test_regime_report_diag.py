"""Tests for regime-report diagnostic CLI."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.regime_report_diag import (
    build_regime_diagnostic,
    format_regime_diagnostic_report,
)


class RegimeReportDiagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "regime.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_report_shape(self) -> None:
        now = int(time.time())
        blob = {
            "market_regime": "RANGE",
            "regime_score": -0.255,
            "fear_greed": 22.0,
            "funding": 54.1,
            "volatility": 50.0,
            "volume": 0.0,
            "trend": 0.0,
            "regime_inputs": {
                "btc_return_pct": -0.03,
                "trend": 0.0,
                "fear_greed": 22.0,
                "funding": 54.1,
                "component_scores": {
                    "btc": 0.0,
                    "trend": 0.0,
                    "fear_greed": -0.35,
                    "funding": -0.2,
                },
            },
        }
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  volatility, volume, trend, funding, market_regime,
                  features_json, gate_decision, gate_expected_pnl_pct,
                  similar_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "g3_signal",
                    1,
                    "BTC",
                    "LONG",
                    50.0,
                    0.0,
                    0.0,
                    54.1,
                    "RANGE",
                    json.dumps(blob),
                    "NEGATIVE_EXPECTANCY",
                    -3.47,
                    24,
                    now,
                ),
            )
            conn.commit()
            data = build_regime_diagnostic(conn, since_ts=now - 10)
            self.assertEqual(data["mix"]["Neutral"], 1)
            self.assertEqual(data["rejected"], 1)
            self.assertTrue(data["blocked_factors"])
            text = format_regime_diagnostic_report(conn, since_hours=1)
            self.assertIn("REGIME REPORT", text)
            self.assertIn("Bull", text)
            self.assertIn("Blocked because:", text)
            self.assertIn("BTC", text)
            self.assertIn("Trend score", text)
            self.assertIn("Reason", text)


if __name__ == "__main__":
    unittest.main()
