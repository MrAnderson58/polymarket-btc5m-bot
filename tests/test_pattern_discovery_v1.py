"""Tests for Pattern Discovery V1."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.pattern_discovery_v1 import (
    build_pattern_discovery,
    run_pattern_discovery,
    write_pattern_exports,
)


class PatternDiscoveryV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "pd.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_clusters_and_exports(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            for i in range(80):
                pnl = 1.5 if i < 30 else (-1.0 if i < 60 else 0.2)
                conn.execute(
                    """
                    INSERT INTO market_events_trade_features_s55 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      funding, trend, fear_greed, oi_delta, volatility, ai_score,
                      features_json, gate_decision, gate_expected_pnl_pct,
                      created_at, closed_at, pnl_pct, result, mfe_pct, mae_pct,
                      duration_sec, market_regime
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "g3", i + 1, "BTCUSDT", "LONG" if i % 2 == 0 else "SHORT",
                        0.01 + (i % 10) * 0.001,
                        -1.0 + (i % 8) * 0.3,
                        20 + (i % 12),
                        float((i % 9) - 4),
                        0.5 + (i % 5) * 0.2,
                        40 + (i % 20),
                        json.dumps({"trend": 0.1}),
                        "ALLOWED", 0.1,
                        now - 3000 + i, now - 1000 + i, pnl,
                        "WIN" if pnl > 0 else "LOSS",
                        abs(pnl) + 0.5, -abs(pnl) * 0.4, 100 + i,
                        "RANGE" if i % 3 else "WEAK_BULL",
                    ),
                )
            conn.commit()

            data = build_pattern_discovery(conn)
            self.assertEqual(data["n_trades"], 80)
            self.assertTrue(data["clusters"])
            self.assertLessEqual(len(data["top"]), 10)
            self.assertLessEqual(len(data["worst"]), 10)
            root = Path(self._tmpdir.name) / "research"
            paths = write_pattern_exports(data, root=root)
            self.assertTrue(paths["patterns_md"].exists())
            self.assertTrue(paths["patterns_json"].exists())
            payload = json.loads(paths["patterns_json"].read_text(encoding="utf-8"))
            self.assertIn("clusters", payload)
            self.assertTrue((root / "patterns").is_dir())
            text = run_pattern_discovery(conn, write_reports=False)
            self.assertIn("PATTERN DISCOVERY V1", text)
            self.assertIn("TOP 10", text)


if __name__ == "__main__":
    unittest.main()
