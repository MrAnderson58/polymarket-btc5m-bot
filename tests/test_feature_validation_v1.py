"""Tests for Feature Validation V1."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.feature_validation_v1 import (
    build_feature_validation,
    run_feature_validation,
    write_feature_validation_report,
)


class FeatureValidationV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "fv.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _insert(self, conn, *, sid: int, pnl: float, funding: float, trend: float, fg: float, direction: str = "LONG") -> None:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55 (
              s40_signal_type, s40_signal_id, symbol, direction,
              funding, trend, fear_greed, oi_delta, volatility, ai_score,
              features_json, gate_decision, gate_expected_pnl_pct,
              created_at, closed_at, pnl_pct, result, market_regime
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "g3", sid, "BTCUSDT", direction,
                funding, trend, fg, funding * 10, 1.0 + trend, 40 + sid,
                json.dumps({"funding": funding, "trend": trend, "fear_greed": fg}),
                "ALLOWED", pnl * 0.5, now - 1000 + sid, now - 500 + sid, pnl,
                "WIN" if pnl > 0 else "LOSS",
                "RANGE" if sid % 2 else "WEAK_BULL",
            ),
        )

    def test_validation_report(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            for i in range(40):
                pnl = 1.0 if i % 3 == 0 else -0.8
                self._insert(
                    conn,
                    sid=i + 1,
                    pnl=pnl,
                    funding=0.01 + (i % 5) * 0.002,
                    trend=-0.5 + (i % 7) * 0.2,
                    fg=20 + (i % 10) * 5,
                    direction="LONG" if i % 2 == 0 else "SHORT",
                )
            conn.commit()
            data = build_feature_validation(conn)
            self.assertEqual(data["n_trades"], 40)
            self.assertTrue(data["singles"])
            self.assertTrue(data["leave_one_out"])
            self.assertTrue(data["interactions"])
            self.assertTrue(data["ranking"])
            self.assertIn("keep", data)
            path = write_feature_validation_report(
                data, root=Path(self._tmpdir.name) / "research",
            )
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("KEEP", text)
            self.assertIn("WATCH", text)
            self.assertIn("REMOVE", text)
            cli = run_feature_validation(conn, write_reports=False)
            self.assertIn("FEATURE VALIDATION V1", cli)


if __name__ == "__main__":
    unittest.main()
