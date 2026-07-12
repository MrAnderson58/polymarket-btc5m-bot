"""Regression tests for signal_quality_report SQL."""

from __future__ import annotations

import tempfile
import time
import unittest

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.reports import (
    signal_quality_report,
    weekly_ranking_report,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


class SignalQualityReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_signal_quality_report_with_ambiguous_confidence_columns(self) -> None:
        """market_events and ai_analyses_f0 both have confidence — must not error."""
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn, symbol="BTC", ret=-5.0)
            now = int(time.time())
            conn.execute(
                "UPDATE market_events SET confidence = 0.55, event_ts = ? WHERE id = ?",
                (now, event_id),
            )
            conn.execute(
                """
                INSERT INTO market_event_ai_analyses_f0 (
                  event_id, prompt_version, response_json, bias, confidence,
                  continuation_probability, reversal_probability, summary_ru, summary_en, created_at
                ) VALUES (?, 'f0', '{}', 'WAIT', 0.72, 0.5, 0.5, 'ru', 'en', ?)
                """,
                (event_id, now),
            )
            conn.commit()
            text = signal_quality_report(conn, days=7)
        self.assertIn("SIGNAL QUALITY REPORT", text)
        self.assertIn("Avg AI confidence: 0.72", text)

    def test_weekly_ranking_report_with_ambiguous_confidence_columns(self) -> None:
        """weekly_ranking_report must qualify a.confidence when joining market_events."""
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn, symbol="ETH", ret=-3.0)
            now = int(time.time())
            conn.execute(
                "UPDATE market_events SET confidence = 0.40, event_ts = ? WHERE id = ?",
                (now, event_id),
            )
            conn.execute(
                """
                INSERT INTO market_event_ai_analyses_f0 (
                  event_id, prompt_version, response_json, bias, confidence,
                  continuation_probability, reversal_probability, summary_ru, summary_en, created_at
                ) VALUES (?, 'f0', '{}', 'WAIT', 0.65, 0.5, 0.5, 'ru', 'en', ?)
                """,
                (event_id, now),
            )
            conn.commit()
            text = weekly_ranking_report(conn)
        self.assertIn("WEEKLY SIGNAL RANKING", text)
        self.assertIn("Average AI confidence: 0.65", text)


if __name__ == "__main__":
    unittest.main()
