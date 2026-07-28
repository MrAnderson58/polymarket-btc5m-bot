"""Tests for unified gate funnel + Fear&Greed research section."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.gate_funnel_report import (
    build_fear_greed_bucket_report,
    build_unified_gate_funnel,
    classify_terminal_stage,
    format_unified_gate_funnel,
)


class UnifiedGateFunnelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self._tmpdir.name) / "funnel.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_stage_attribution_no_contradiction(self) -> None:
        self.assertEqual(classify_terminal_stage("REGIME_BLOCK", None)[0], "REGIME")
        self.assertEqual(classify_terminal_stage("NEGATIVE_EXPECTANCY", None)[0], "EXPECTANCY")
        self.assertEqual(classify_terminal_stage("MAX_OPEN", None)[0], "RISK")
        self.assertEqual(classify_terminal_stage("INSUFFICIENT_HISTORY", 1)[0], "OPEN")

    def test_funnel_and_fg_report(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            # expectancy fail
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  fear_greed, market_regime, features_json, gate_decision,
                  gate_expected_pnl_pct, similar_count, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3_signal", 1, "BTC", "LONG", 22.0, "RANGE",
                    json.dumps({"fear_greed": 22.0, "regime_score": -0.2}),
                    "NEGATIVE_EXPECTANCY", -3.5, 24, now,
                ),
            )
            # open via cold start
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction, paper_trade_id,
                  fear_greed, market_regime, features_json, gate_decision,
                  gate_expected_pnl_pct, similar_count, created_at,
                  closed_at, pnl_pct, result
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3_signal", 2, "ETH", "LONG", 99, 22.0, "RANGE",
                    json.dumps({"fear_greed": 22.0}),
                    "INSUFFICIENT_HISTORY", 0.0, 5, now,
                    now, -1.0, "LOSS",
                ),
            )
            # closed mid fear for bucket
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  s40_signal_type, s40_signal_id, symbol, direction, paper_trade_id,
                  fear_greed, market_regime, features_json, gate_decision,
                  gate_expected_pnl_pct, similar_count, created_at,
                  closed_at, pnl_pct, pnl_usd, result
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "g3_signal", 3, "SOL", "SHORT", 100, 60.0, "WEAK_BULL",
                    json.dumps({"fear_greed": 60.0}),
                    "ALLOWED", 1.0, 40, now - 10,
                    now, 2.0, 5.0, "WIN",
                ),
            )
            conn.commit()

            funnel = build_unified_gate_funnel(conn, since_ts=now - 100)
            self.assertEqual(funnel["n_event"], 3)
            stages = {s["stage"]: s for s in funnel["stages"]}
            self.assertEqual(stages["REGIME"]["failed"], 0)
            self.assertEqual(stages["EXPECTANCY"]["failed"], 1)
            self.assertEqual(stages["OPEN"]["passed"], 2)

            fg = build_fear_greed_bucket_report(conn, since_ts=now - 86400 * 30)
            self.assertEqual(fg["buckets"]["<25"]["n"], 1)
            self.assertEqual(fg["buckets"]["50–75"]["n"], 1)

            text = format_unified_gate_funnel(conn, since_hours=1, fear_greed_months=1)
            self.assertIn("UNIFIED GATE FUNNEL", text)
            self.assertIn("EXPECTANCY", text)
            self.assertIn("FEAR & GREED RESEARCH", text)
            self.assertIn("NEGATIVE_EXPECTANCY", text)


if __name__ == "__main__":
    unittest.main()
