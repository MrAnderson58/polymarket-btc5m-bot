"""Phase F.4.1 — Telegram dedupe and final alert tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.ai_analyst.job_queue import enqueue_analysis_job, process_pending_jobs
from bot.research.market_events.ai_analyst.provider import DeterministicShadowProvider
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.market_event_alerts import (
    ALERT_AI,
    ALERT_SHOCK,
    alert_ai_research_note,
    alert_shock_detected,
)
from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import (
    STAGE_FINAL_SENT,
    duplicate_prevented_count,
    merge_ai_into_shock_message,
)


class TelegramDedupeF41Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_f41.db"
        self._env_patch = patch.dict(os.environ, {
            "ME_TELEGRAM_ALERTS_ENABLED": "true",
            "ME_ALERT_SHOCK": "true",
            "ME_AI_ANALYST_ENABLED": "true",
            "ME_AI_PROVIDER": "deterministic",
            "ME_ALERT_AI_COMMENTARY": "true",
            "ME_TREND_SHOCK_DEFER_ALERT": "false",
            "ME_F5_PROFESSIONAL_SIGNAL": "false",
        }, clear=False)
        self._env_patch.start()
        self._f41_patch = patch(
            "bot.research.market_events.signal_intelligence.config.F41_TELEGRAM_DEDUPE",
            True,
        )
        self._f41_patch.start()
        self._f5_patch = patch(
            "bot.research.market_events.signal_intelligence.config.F5_ENABLED",
            False,
        )
        self._f5_patch.start()

    def tearDown(self) -> None:
        self._f5_patch.stop()
        self._f41_patch.stop()
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _seed_event(self, conn) -> int:
        now = int(time.time())
        return int(conn.execute(
            """
            INSERT INTO market_events (
              event_ts, detected_ts, venue, symbol, direction, phase,
              trigger_window_seconds, return_pct, classification,
              btc_return_pct, relative_return_pct, volume_zscore,
              session_regime, asset_class, detector_version,
              detector_triggers_json, dedup_key, created_at
            ) VALUES (?, ?, 'binance_futures', 'SOL', 'DOWN', 'SHOCK_DETECTED',
              60, -3.5, 'ASSET_SPECIFIC', 0.2, -3.7, 2.1,
              'US', 'CRYPTO', 'v1', '["SHOCK_A"]', ?, ?)
            """,
            (now, now, f"dedup-f41-{now}", now),
        ).lastrowid)

    def _seed_ai_analysis(self, conn, event_id: int, note: str = "SOL shock looks extended") -> None:
        payload = {
            "short_commentary": note,
            "movement_interpretation": "OVERREACTION",
            "reversal_bias": "FADE_FAVORED",
            "confidence": 0.72,
            "analysis_status": "COMPLETE",
        }
        conn.execute(
            """
            INSERT INTO market_event_ai_analyses (
              event_id, job_id, provider, model, prompt_version,
              structured_output_json, movement_interpretation, reversal_bias,
              confidence, context_ids_json, latency_ms, token_usage_json, created_at
            ) VALUES (?, NULL, 'test', 'test', 'v1', ?, ?, ?, ?, '[]', 1.0, '{}', ?)
            """,
            (
                event_id,
                json.dumps(payload),
                payload["movement_interpretation"],
                payload["reversal_bias"],
                payload["confidence"],
                int(time.time()),
            ),
        )

    def test_schema_v18(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v18", applied)
            self.assertEqual(SCHEMA_VERSION, 39)
            cols = {
                r[1] for r in conn.execute("PRAGMA table_info(market_event_alert_log)").fetchall()
            }
            self.assertIn("message_type", cols)
            self.assertIn("telegram_message_stage", cols)
            self.assertIn("duplicate_prevented", cols)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True)
    def test_shock_sent_once_with_stage(self, _shock, _alerts, mock_send) -> None:
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            self.assertTrue(alert_shock_detected(conn, eid))
            self.assertFalse(alert_shock_detected(conn, eid))
            row = conn.execute(
                """
                SELECT message_type, telegram_message_stage, duplicate_prevented, sent
                FROM market_event_alert_log
                WHERE event_id = ? AND sent = 1
                """,
                (eid,),
            ).fetchone()
            self.assertEqual(row["message_type"], "SHOCK")
            self.assertEqual(row["telegram_message_stage"], STAGE_FINAL_SENT)
            self.assertEqual(int(row["duplicate_prevented"]), 0)
            prevented = conn.execute(
                "SELECT COUNT(*) FROM market_event_alert_log WHERE event_id = ? AND duplicate_prevented = 1",
                (eid,),
            ).fetchone()[0]
            self.assertEqual(prevented, 1)
            self.assertEqual(mock_send.call_count, 1)
            self.assertEqual(duplicate_prevented_count(conn), 1)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True)
    def test_ai_note_never_sent_standalone(self, _shock, _alerts, mock_send) -> None:
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            for _ in range(100):
                self.assertFalse(alert_ai_research_note(conn, eid, "AI RESEARCH NOTE body"))
            n_ai = conn.execute(
                "SELECT COUNT(*) FROM market_event_alert_log WHERE alert_type = ?",
                (ALERT_AI,),
            ).fetchone()[0]
            self.assertEqual(n_ai, 0)
            self.assertEqual(mock_send.call_count, 0)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True)
    @patch("bot.research.market_events.ai_analyst.job_queue.AI_ENABLED", True)
    @patch("bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider")
    def test_ai_worker_100_iterations_one_telegram(
        self, mock_provider, _shock, _alerts, mock_send,
    ) -> None:
        mock_provider.return_value = DeterministicShadowProvider()
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            enqueue_analysis_job(conn, event_id=eid)
            for _ in range(100):
                process_pending_jobs(conn, max_jobs=1)
                alert_ai_research_note(conn, eid, "shadow note")
            self.assertTrue(alert_shock_detected(conn, eid))
            for _ in range(99):
                alert_shock_detected(conn, eid)
            sent_rows = conn.execute(
                "SELECT COUNT(*) FROM market_event_alert_log WHERE event_id = ? AND sent = 1",
                (eid,),
            ).fetchone()[0]
            self.assertEqual(sent_rows, 1)
            self.assertEqual(mock_send.call_count, 1)
            ai_rows = conn.execute(
                "SELECT COUNT(*) FROM market_event_alert_log WHERE alert_type = ?",
                (ALERT_AI,),
            ).fetchone()[0]
            self.assertEqual(ai_rows, 0)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True)
    def test_ai_merged_into_shock_message(self, _shock, _alerts, mock_send) -> None:
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            self._seed_ai_analysis(conn, eid, "Fade setup after liquidation flush")
            alert_shock_detected(conn, eid)
            msg = mock_send.call_args[0][0]
            self.assertIn("Fade setup after liquidation flush", msg)

    def test_merge_ai_helper_idempotent(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            self._seed_ai_analysis(conn, eid, "Unique AI line")
            base = "SHOCK body"
            once = merge_ai_into_shock_message(conn, eid, base)
            twice = merge_ai_into_shock_message(conn, eid, once)
            self.assertEqual(once, twice)
            self.assertIn("Unique AI line", once)


if __name__ == "__main__":
    unittest.main()
