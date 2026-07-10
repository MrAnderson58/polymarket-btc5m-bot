"""Phase E.3.3 Telegram alerts and AI analyst shadow layer tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.market_events.ai_analyst.config import PROMPT_VERSION
from bot.research.market_events.ai_analyst.context_bundle import build_context_bundle
from bot.research.market_events.ai_analyst.job_queue import (
    JOB_COMPLETE,
    JOB_PENDING,
    enqueue_analysis_job,
    process_pending_jobs,
)
from bot.research.market_events.ai_analyst.provider import (
    AnalysisResult,
    DeterministicShadowProvider,
)
from bot.research.market_events.ai_analyst.reports import (
    ai_analysis_audit,
    ai_event_report,
    market_alert_audit,
)
from bot.research.market_events.ai_analyst.schema import (
    parse_model_response,
    parse_structured_analysis,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.market_event_alerts import (
    ALERT_SHOCK,
    alert_paper_position_update,
    alert_reversal_confirmed,
    alert_shock_detected,
    format_shock_alert,
)
from bot.research.market_events.paper_runner import ShockPaperRunner
from bot.research.market_events.shock_detector import ShockCandidate, ShockTrigger


class MarketEventsE33Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e33.db"
        self._env_patch = patch.dict(os.environ, {
            "ME_TELEGRAM_ALERTS_ENABLED": "true",
            "ME_ALERT_SHOCK": "true",
            "ME_ALERT_REVERSAL": "true",
            "ME_ALERT_PAPER_UPDATES": "true",
            "ME_AI_ANALYST_ENABLED": "true",
            "ME_AI_PROVIDER": "deterministic",
        }, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _seed_event(self, conn, *, symbol: str = "SOL", dedup: str | None = None) -> int:
        now = int(time.time())
        dk = dedup or f"dedup-e33-{now}-{symbol}"
        return int(conn.execute(
            """
            INSERT INTO market_events (
              event_ts, detected_ts, venue, symbol, direction, phase,
              trigger_window_seconds, return_pct, classification,
              btc_return_pct, relative_return_pct, volume_zscore,
              session_regime, asset_class, detector_version,
              detector_triggers_json, dedup_key, created_at
            ) VALUES (?, ?, 'binance_futures', ?, 'DOWN', 'SHOCK_DETECTED',
              60, -3.5, 'ASSET_SPECIFIC', 0.2, -3.7, 2.1,
              'US', 'CRYPTO', 'v1', '["SHOCK_A"]', ?, ?)
            """,
            (now, now, symbol, dk, now),
        ).lastrowid)

    def test_schema_v7_migration(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v7", applied)
            self.assertEqual(SCHEMA_VERSION, 7)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_event_alert_log", tables)
            self.assertIn("market_event_analysis_jobs", tables)
            self.assertIn("market_event_ai_analyses", tables)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True)
    def test_shock_sends_exactly_one_alert(self, _shock, _alerts, mock_send) -> None:
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            self.assertTrue(alert_shock_detected(conn, eid))
            self.assertFalse(alert_shock_detected(conn, eid))
            n = conn.execute(
                "SELECT COUNT(*) FROM market_event_alert_log WHERE alert_type=?",
                (ALERT_SHOCK,),
            ).fetchone()[0]
            self.assertEqual(n, 1)
            self.assertEqual(mock_send.call_count, 1)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True)
    def test_restart_does_not_resend_shock_alert(self, _shock, _alerts, mock_send) -> None:
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            alert_shock_detected(conn, eid)
            conn.commit()
        with self._conn() as conn:
            alert_shock_detected(conn, eid)
            self.assertEqual(mock_send.call_count, 1)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_reversal_enabled", return_value=True)
    def test_reversal_sends_once_per_variant(self, _rev, _alerts, mock_send) -> None:
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            self.assertTrue(alert_reversal_confirmed(
                conn, event_id=eid, reversal_variant="R1", paper_runs=5,
            ))
            self.assertFalse(alert_reversal_confirmed(
                conn, event_id=eid, reversal_variant="R1", paper_runs=5,
            ))
            self.assertTrue(alert_reversal_confirmed(
                conn, event_id=eid, reversal_variant="R2", paper_runs=5,
            ))
            self.assertEqual(mock_send.call_count, 2)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_shock_enabled", return_value=True)
    def test_notifier_failure_does_not_crash(self, _shock, _alerts, mock_send) -> None:
        mock_send.side_effect = RuntimeError("network down")
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            alert_shock_detected(conn, eid)
            row = conn.execute(
                "SELECT sent, error FROM market_event_alert_log WHERE event_id=?",
                (eid,),
            ).fetchone()
            self.assertEqual(int(row["sent"]), 0)
            self.assertIsNotNone(row["error"])

    def test_paper_label_in_messages(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            msg = format_shock_alert(conn, eid)
            self.assertIn("PAPER", msg)
            self.assertIn("EVENT", msg)
            self.assertIn("MARKET", msg)
            self.assertIn("HISTORY", msg)

    @patch("bot.research.market_events.market_event_alerts._send_telegram")
    @patch("bot.research.market_events.market_event_alerts.alerts_enabled", return_value=True)
    @patch("bot.research.market_events.market_event_alerts.alert_paper_updates_enabled", return_value=True)
    def test_paper_update_alert_label(self, _paper, _alerts, mock_send) -> None:
        mock_send.return_value = (True, None)
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            alert_paper_position_update(
                conn, event_id=eid, update_type="TP",
                symbol="SOL", reversal_variant="R1", exit_variant="E1",
            )
            row = conn.execute(
                "SELECT message_text FROM market_event_alert_log WHERE event_id=?",
                (eid,),
            ).fetchone()
            self.assertIn("PAPER", row["message_text"])

    def test_enqueue_analysis_job_on_event(self) -> None:
        with patch("bot.research.market_events.ai_analyst.job_queue.AI_ENABLED", True):
            with self._conn() as conn:
                apply_migrations(conn)
                eid = self._seed_event(conn)
                jid = enqueue_analysis_job(conn, event_id=eid)
                self.assertIsNotNone(jid)
                dup = enqueue_analysis_job(conn, event_id=eid)
                self.assertEqual(jid, dup)
                n = conn.execute(
                    "SELECT COUNT(*) FROM market_event_analysis_jobs WHERE event_id=?",
                    (eid,),
                ).fetchone()[0]
                self.assertEqual(n, 1)

    @patch("bot.research.market_events.ai_analyst.job_queue.AI_ENABLED", True)
    @patch("bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider")
    def test_process_job_persists_analysis(self, mock_provider) -> None:
        provider = DeterministicShadowProvider()
        mock_provider.return_value = provider
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            jid = enqueue_analysis_job(conn, event_id=eid)
            n = process_pending_jobs(conn, max_jobs=1)
            self.assertEqual(n, 1)
            row = conn.execute(
                "SELECT status FROM market_event_analysis_jobs WHERE id=?",
                (jid,),
            ).fetchone()
            self.assertEqual(row["status"], JOB_COMPLETE)
            analysis = conn.execute(
                "SELECT reversal_bias FROM market_event_ai_analyses WHERE event_id=?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(analysis)

    def test_malformed_model_output_rejected(self) -> None:
        self.assertIsNone(parse_model_response("not json", event_id=1, symbol="SOL"))
        self.assertIsNone(parse_model_response('{"confidence": "bad"}', event_id=1, symbol="SOL"))

    def test_insufficient_context_status(self) -> None:
        from bot.research.market_events.ai_analyst.provider import NullAnalystProvider

        result = NullAnalystProvider().analyze_market_event({"event_id": 1, "market_event": {}})
        self.assertEqual(result.analysis.analysis_status, "INSUFFICIENT_CONTEXT")

    @patch("bot.research.market_events.ai_analyst.job_queue.AI_ENABLED", True)
    @patch("bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider")
    def test_provider_failure_does_not_affect_lifecycle(self, mock_provider) -> None:
        mock_provider.return_value = MagicMock(
            analyze_market_event=MagicMock(return_value=AnalysisResult(
                analysis=parse_structured_analysis(
                    {
                        "analysis_status": "FAILED",
                        "movement_interpretation": "UNKNOWN",
                        "reversal_bias": "NO_VIEW",
                        "confidence": 0.0,
                    },
                    event_id=1,
                    symbol="SOL",
                ),
                provider="test",
                model="test",
                prompt_version=PROMPT_VERSION,
                latency_ms=1.0,
                error="timeout",
            )),
        )
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            enqueue_analysis_job(conn, event_id=eid)
            process_pending_jobs(conn, max_jobs=1)
            phase = conn.execute(
                "SELECT phase FROM market_events WHERE id=?",
                (eid,),
            ).fetchone()["phase"]
            self.assertEqual(phase, "SHOCK_DETECTED")

    def test_context_bundle_bounded(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            for i in range(20):
                conn.execute(
                    """
                    INSERT INTO market_event_context (
                      event_id, context_type, source, source_record_id,
                      context_ts, time_delta_seconds, relevance_score, context_json, created_at
                    ) VALUES (?, 'commentary', 'signalyp', ?, ?, 0, ?, '{}', ?)
                    """,
                    (eid, str(i), int(time.time()), 1.0 - i * 0.01, int(time.time())),
                )
            bundle = build_context_bundle(conn, eid)
            self.assertLessEqual(len(bundle["telegram_context"]), 8)

    def test_context_ids_preserved_in_analysis(self) -> None:
        provider = DeterministicShadowProvider()
        bundle = {
            "event_id": 1,
            "market_event": {"symbol": "SOL", "classification": "ASSET_SPECIFIC", "return_pct": -2.0},
            "price_structure": {"confirmed_reversal": None},
            "telegram_context": [{"context_id": "post:99", "type": "signal"}],
        }
        result = provider.analyze_market_event(bundle)
        self.assertIn("post:99", result.analysis.relevant_context_ids)

    def test_collector_never_waits_for_ai(self) -> None:
        feed = MagicMock()
        feed.poll_universe.return_value = {}
        feed.get_state.return_value = None
        runner = ShockPaperRunner(max_cycles=1, feed=feed)
        t0 = time.perf_counter()
        with self._conn() as conn:
            apply_migrations(conn)
            with patch(
                "bot.research.market_events.ai_analyst.analysis_runner.run_analysis_job",
                side_effect=lambda *a, **k: time.sleep(5),
            ) as mock_run:
                runner.run_once(conn, ["BTC"])
                mock_run.assert_not_called()
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0)

    @patch("bot.research.market_events.ai_analyst.job_queue.AI_ENABLED", True)
    def test_ai_output_cannot_create_paper_trade(self) -> None:
        """Structured analysis has no code path into paper_strategy_runs."""
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            enqueue_analysis_job(conn, event_id=eid)
            with patch(
                "bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider",
                return_value=DeterministicShadowProvider(),
            ):
                process_pending_jobs(conn, max_jobs=1)
            n = conn.execute(
                "SELECT COUNT(*) FROM paper_strategy_runs WHERE event_id=?",
                (eid,),
            ).fetchone()[0]
            self.assertEqual(n, 0)

    def test_audit_cli_reports(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self.assertIn("MARKET ALERT AUDIT", market_alert_audit(conn, days=1))
            self.assertIn("AI ANALYSIS AUDIT", ai_analysis_audit(conn, days=1))
            self.assertIn("AI EVENT REPORT", ai_event_report(conn, days=7))

    @patch("bot.research.market_events.ai_analyst.job_queue.AI_ENABLED", True)
    def test_shock_detect_enqueues_analysis_job(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            from bot.research.market_events.ai_analyst.job_queue import enqueue_analysis_job as enqueue

            enqueue(conn, event_id=eid)
            row = conn.execute(
                "SELECT id FROM market_event_analysis_jobs WHERE event_id=?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
