"""Tests for Trading AI Agent v1 (observe-only)."""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from bot.ai_agent.decision import compute_ai_score, compute_decision
from bot.ai_agent.learning import run_daily_learning
from bot.ai_agent.daily import build_daily_report
from bot.ai_agent.models import RuleBasedPredictor, list_models
from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
)


class AIAgentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed(self, conn) -> None:
        now = int(time.time())
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug="btc-updown-5m-agent",
            window_start_ts=now - 120,
            end_ts=now + 180,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.38,
            entry_ts=now - 60,
        )
        close_early_reversion_v2_trade(
            conn,
            trade_id,
            exit_price=0.42,
            exit_reason="TRAILING_STOP",
            pnl_percent=10.0,
            pnl_usdc=0.2,
            holding_time_seconds=40.0,
        )
        checked_at = datetime.fromtimestamp(now, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        for offset, bid in ((0, 0.38), (20, 0.41), (40, 0.42)):
            conn.execute(
                """
                INSERT INTO market_checks (
                    market_slug, seconds_remaining, strike_price, btc_price,
                    yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime(?, 'unixepoch'))
                """,
                (
                    "btc-updown-5m-agent",
                    200.0 - offset,
                    100_000.0,
                    100_000.0 - offset,
                    0.5,
                    0.51,
                    bid,
                    bid + 0.01,
                    None,
                    now - 60 + offset,
                ),
            )

    def test_sync_and_score(self) -> None:
        state = Path(self._tmpdir.name) / "state.json"
        with mock.patch("bot.ai_agent.learning._state_path", return_value=state):
            with mock.patch("bot.ai_agent.journal.journal_root", return_value=Path(self._tmpdir.name) / "j"):
                with connect(self.db_path) as conn:
                    self._seed(conn)
                    conn.commit()
                    run_daily_learning(conn)
                    from bot.ai_agent.memory import load_ai_features

                    rows = load_ai_features(conn)
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0]["decision"], ("ALLOW", "SKIP", "SHADOW"))
        self.assertGreaterEqual(float(rows[0]["ai_score"]), 0)

    def test_rule_based_predictor(self) -> None:
        pred = RuleBasedPredictor()
        score = pred.predict_score({"entry_price": 0.38, "side": "NO", "spread": 0.01})
        self.assertGreater(score, 0)
        self.assertEqual(compute_decision(score), compute_decision(compute_ai_score(
            {"entry_price": 0.38, "side": "NO", "spread": 0.01}
        )))

    def test_daily_report(self) -> None:
        state = Path(self._tmpdir.name) / "state.json"
        with mock.patch("bot.ai_agent.learning._state_path", return_value=state):
            with mock.patch("bot.ai_agent.journal.journal_root", return_value=Path(self._tmpdir.name) / "j"):
                with connect(self.db_path) as conn:
                    self._seed(conn)
                    conn.commit()
                    run_daily_learning(conn)
                    conn.commit()
                    report = build_daily_report(conn)
        self.assertEqual(report["meta"]["mode"], "observe_only")
        self.assertIn("score_distribution", report)
        self.assertIn("intelligence", report)
        self.assertTrue(any(m["name"] == "rule_based" for m in list_models()))


if __name__ == "__main__":
    unittest.main()
