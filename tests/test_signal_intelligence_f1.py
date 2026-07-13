"""Phase F.1 Telegram signal intelligence tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.market_event_alerts import format_shock_alert
from bot.research.market_events.signal_intelligence.confidence_f1 import (
    CONFIDENCE_WEIGHTS,
    ENTRY_WAIT_R2,
    VALID_ENTRY_RECOMMENDATIONS,
)
from bot.research.market_events.signal_intelligence.outcome_f1 import record_outcome_f1
from bot.research.market_events.signal_intelligence.signal_report_f1 import (
    build_signal_report_f1,
    compute_confidence,
    persist_signal_report_f1,
    run_signal_report_f1,
)
from bot.research.market_events.signal_intelligence.telegram_f1 import (
    format_result_f1,
    format_shock_f1,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


class SignalIntelligenceF1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schema_v13(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v18", applied)
            self.assertEqual(SCHEMA_VERSION, 31)

    def test_confidence_weights_sum_to_ten(self) -> None:
        self.assertAlmostEqual(sum(CONFIDENCE_WEIGHTS.values()), 10.0)

    def test_compute_confidence_bounded(self) -> None:
        components = {k: 1.0 for k in CONFIDENCE_WEIGHTS}
        score, breakdown = compute_confidence(components)
        self.assertLessEqual(score, 10.0)
        self.assertGreaterEqual(score, 9.0)
        self.assertEqual(len(breakdown), len(CONFIDENCE_WEIGHTS))

    def test_build_and_persist_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=-8.4)
            report = build_signal_report_f1(conn, eid)
            self.assertIsNotNone(report)
            self.assertGreaterEqual(report.confidence_score, 0)
            self.assertLessEqual(report.confidence_score, 10)
            self.assertIn(report.entry_recommendation, VALID_ENTRY_RECOMMENDATIONS)
            persist_signal_report_f1(conn, report)
            row = conn.execute(
                "SELECT * FROM market_events_signal_reports_f1 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)
            expl = json.loads(row["explanation_json"])
            self.assertIn("reasons_ru", expl)

    def test_run_signal_report_via_hook_path(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            rep = run_signal_report_f1(conn, eid)
            self.assertIsNotNone(rep)

    def test_format_shock_f1_russian(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=-8.4)
            run_signal_report_f1(conn, eid)
            text = format_shock_f1(conn, eid)
            self.assertIn("SHOCK", text)
            self.assertIn("Уверенность", text)
            self.assertIn("Почему:", text)
            self.assertIn("PAPER ONLY", text)
            self.assertIn("WAIT", text)

    def test_format_shock_alert_uses_f1(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=-7.0)
            run_signal_report_f1(conn, eid)
            text = format_shock_alert(conn, eid)
            self.assertIn("Уверенность", text)

    def test_entry_recommendation_default_wait_r2(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=-5.0)
            report = build_signal_report_f1(conn, eid)
            self.assertIsNotNone(report)
            self.assertIn(report.entry_recommendation, VALID_ENTRY_RECOMMENDATIONS)

    def test_format_result_f1(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            run_signal_report_f1(conn, eid)
            text = format_result_f1(
                conn, event_id=eid, symbol="SUI", pnl_pct=3.2,
                holding_min=18, exit_variant="TP",
            )
            self.assertIn("RESULT", text)
            self.assertIn("+3.2%", text)
            self.assertIn("PAPER ONLY", text)

    def test_record_outcome_f1(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            run_signal_report_f1(conn, eid)
            record_outcome_f1(conn, event_id=eid, paper_run_id=None, pnl_pct=2.5, holding_seconds=900)
            n = conn.execute(
                "SELECT COUNT(*) FROM market_events_signal_outcomes_f1 WHERE event_id = ?",
                (eid,),
            ).fetchone()[0]
            self.assertEqual(int(n), 1)

    def test_explanation_has_historical_rate(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            now = int(time.time())
            for i, ret in enumerate([-8.0, -7.5, -9.0]):
                conn.execute(
                    """
                    INSERT INTO market_events (
                      event_ts, detected_ts, venue, symbol, direction, phase,
                      trigger_window_seconds, return_pct, classification,
                      detector_version, detector_triggers_json, dedup_key, created_at
                    ) VALUES (?, ?, 'binance_futures', 'SUI', 'DOWN', 'SHOCK_DETECTED',
                      900, ?, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
                    """,
                    (now - 3600 * (i + 1), now, ret, f"hist-{i}", now),
                )
            eid = seed_event(conn, ret=-8.2)
            report = build_signal_report_f1(conn, eid)
            self.assertIsNotNone(report)
            self.assertGreater(len(report.matching_events), 0)
            self.assertIn("reasons_ru", report.explanation)


if __name__ == "__main__":
    unittest.main()
