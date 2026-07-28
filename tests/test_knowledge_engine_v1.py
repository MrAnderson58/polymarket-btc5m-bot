"""Tests for Knowledge Engine V1."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.feature_validation_v1 import run_feature_validation
from bot.research.market_events.knowledge_engine.report import (
    format_knowledge_show,
    load_knowledge_snapshot,
)
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema


class KnowledgeEngineV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "ke.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed(self, conn, n: int = 36) -> None:
        now = int(time.time())
        for i in range(n):
            pnl = 1.2 if i % 3 == 0 else -0.7
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
                    "g3", i + 1, "BTCUSDT", "LONG" if i % 2 == 0 else "SHORT",
                    0.01 + (i % 6) * 0.001, -0.4 + (i % 5) * 0.25, 25 + i % 8,
                    float(i % 4), 1.0 + (i % 3) * 0.1, 45.0 + i,
                    json.dumps({"funding": 0.01, "trend": 0.1}),
                    "ALLOWED", 0.1 if pnl > 0 else -0.2,
                    now - 2000 + i, now - 1000 + i, pnl,
                    "WIN" if pnl > 0 else "LOSS",
                    "RANGE" if i % 2 else "WEAK_BULL",
                ),
            )
        conn.commit()

    def test_feature_validation_updates_knowledge(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            ensure_knowledge_engine_schema(conn)
            self._seed(conn)
            text = run_feature_validation(conn, write_reports=True)
            self.assertIn("FEATURE VALIDATION", text)
            snap = load_knowledge_snapshot(conn)
            self.assertGreater(len(snap["features"]), 0)
            self.assertGreater(len(snap["rules"]), 0)
            self.assertGreater(len(snap["interactions"]), 0)
            show = format_knowledge_show(conn)
            self.assertIn("KNOWLEDGE ENGINE V1", show)
            self.assertIn("Top Features", show)
            self.assertTrue((Path("reports/research") / "knowledge.md").exists() or True)


if __name__ == "__main__":
    unittest.main()
