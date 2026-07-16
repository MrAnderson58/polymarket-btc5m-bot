"""FIX-S4.2 — learning worker throughput (bounded reviews + Claude timeout)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
    MAX_REVIEWS_PER_CYCLE,
    REVIEW_STATUS_PENDING_AI,
    REVIEW_TYPE_PLACEHOLDER,
    _generate_review_text_s40,
    _placeholder_review_text_s40,
    run_learning_reviews_s40_once,
    run_learning_worker_s40,
)


class TestLearningThroughputS42(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "s42.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_placeholder_format(self) -> None:
        text = _placeholder_review_text_s40(
            signal_row={"direction": "LONG"},
            snapshot={
                "snapshot_news_impact": "Bullish",
                "snapshot_pattern_json": '{"pattern": "Compression"}',
            },
        )
        self.assertIn("Claude unavailable.", text)
        self.assertIn("Market:\nLONG", text)
        self.assertIn("News:\nBullish", text)
        self.assertIn("Pattern:\nCompression", text)
        self.assertIn("Auto review postponed.", text)

    def test_timeout_skips_without_persist(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.signal_learning_s40.is_claude_configured",
            return_value=True,
        ), patch(
            "bot.research.market_events.signal_intelligence.signal_learning_s40._call_claude_review_bounded_s40",
            side_effect=TimeoutError("timeout"),
        ):
            text, _outcome, status, rtype, timed_out = _generate_review_text_s40(
                signal_type="g3_signal",
                signal_row={
                    "symbol": "BTC",
                    "direction": "LONG",
                    "entry": 1.0,
                    "stop": 0.9,
                    "tp1": 1.1,
                    "tp2": 1.2,
                    "timestamp": int(time.time()),
                },
                snapshot={},
                checkpoints=[],
                timeout_sec=1,
            )
        self.assertTrue(timed_out)
        self.assertEqual(text, "")
        self.assertEqual(status, REVIEW_STATUS_PENDING_AI)
        self.assertEqual(rtype, REVIEW_TYPE_PLACEHOLDER)

    def test_claude_unavailable_writes_placeholder(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.signal_learning_s40.is_claude_configured",
            return_value=False,
        ):
            text, _outcome, status, rtype, timed_out = _generate_review_text_s40(
                signal_type="g3_signal",
                signal_row={"symbol": "ETH", "direction": "SHORT"},
                snapshot={"snapshot_news_impact": "Hack"},
                checkpoints=[],
            )
        self.assertFalse(timed_out)
        self.assertEqual(status, REVIEW_STATUS_PENDING_AI)
        self.assertEqual(rtype, REVIEW_TYPE_PLACEHOLDER)
        self.assertIn("Claude unavailable.", text)

    def test_worker_one_cycle_fast(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        t0 = time.perf_counter()
        stats = run_learning_worker_s40(
            max_cycles=1,
            max_reviews_per_cycle=MAX_REVIEWS_PER_CYCLE,
            claude_timeout_sec=2,
        )
        elapsed = time.perf_counter() - t0
        self.assertEqual(stats["errors"], 0)
        self.assertLess(elapsed, 30.0)

    def test_reviews_limit_default(self) -> None:
        self.assertEqual(MAX_REVIEWS_PER_CYCLE, 5)
        with market_events_connection() as conn:
            apply_migrations(conn)
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(market_events_signal_learning_s40_reviews)").fetchall()
            }
        self.assertIn("review_status", cols)
        self.assertIn("review_type", cols)


if __name__ == "__main__":
    unittest.main()
