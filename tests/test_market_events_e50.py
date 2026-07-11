"""Phase E.5 Telegram Alert Engine and Dashboard tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.alert_engine.ai_comparison import run_ai_comparison
from bot.research.market_events.alert_engine.daily_digest import build_daily_digest
from bot.research.market_events.alert_engine.format_v2 import (
    format_paper_result_v2,
    format_reversal_alert_v2,
    format_shock_alert_v2,
)
from bot.research.market_events.alert_engine.heartbeat import build_heartbeat_message
from bot.research.market_events.alert_engine.opportunity_score import compute_opportunity_score
from bot.research.market_events.alert_engine.timeline import build_event_timeline, format_timeline_text
from bot.research.market_events.alert_engine.weekly_report import build_weekly_report
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.market_event_alerts import format_shock_alert


class MarketEventsE50Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e50.db"
        self._env = patch.dict(os.environ, {"ME_ALERT_LOCALE": "en"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def _seed_event(self, conn, *, symbol: str = "BTC") -> int:
        now = int(time.time())
        return int(conn.execute(
            """
            INSERT INTO market_events (
              event_ts, detected_ts, venue, symbol, direction, phase,
              trigger_window_seconds, return_pct, classification,
              btc_return_pct, relative_return_pct, volume_zscore,
              session_regime, asset_class, detector_version,
              detector_triggers_json, dedup_key, created_at
            ) VALUES (?, ?, 'binance_futures', ?, 'DOWN', 'MONITORING_REVERSAL',
              240, -3.21, 'MARKET_WIDE', -2.0, -1.2, 2.5,
              'US', 'CRYPTO', 'v1', '["SHOCK_A"]', ?, ?)
            """,
            (now, now, symbol, f"dedup-e50-{now}", now),
        ).lastrowid)

    def test_schema_v9_migration(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v9", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 9)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            for t in (
                "market_events_opportunity_scores",
                "market_events_ai_comparisons",
                "market_events_digest_log",
                "market_events_timeline_cache",
                "market_events_scheduler_state",
            ):
                self.assertIn(t, tables)

    def test_shock_alert_format_v2(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            msg = format_shock_alert_v2(conn, eid)
            self.assertIn("🚨", msg)
            self.assertIn("Symbol:", msg)
            self.assertIn("PAPER ONLY", msg)
            self.assertIn("BTC", msg)

    def test_reversal_alert_format_v2(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO paper_strategy_runs (
                  event_id, strategy_name, strategy_version, reversal_variant, exit_variant,
                  eligibility, entry_ts, entry_price, initial_stop, created_at
                ) VALUES (?, 'test', 'v1', 'R2', 'EXIT_B', 1, ?, 68422, 68180, ?)
                """,
                (eid, now, now),
            )
            msg = format_reversal_alert_v2(
                conn, event_id=eid, reversal_variant="R2",
                confirm_latency_sec=252, extreme_price=None, path_json=None, paper_runs=1,
            )
            self.assertIn("✅", msg)
            self.assertIn("R2", msg)
            self.assertIn("68422", msg)

    def test_paper_result_format_v2(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO paper_strategy_runs (
                  event_id, strategy_name, strategy_version, reversal_variant, exit_variant,
                  eligibility, entry_ts, exit_ts, gross_return, net_return,
                  duration_seconds, exit_reason, created_at
                ) VALUES (?, 'test', 'v1', 'R2', 'EXIT_B', 1, ?, ?, 0.84, 0.84, 1080, 'TP', ?)
                """,
                (eid, now - 1080, now, now),
            )
            msg = format_paper_result_v2(
                conn, event_id=eid, symbol="BTC", reversal_variant="R2",
                exit_variant="EXIT_B", update_type="TP",
            )
            self.assertIn("🏁", msg)
            self.assertIn("+0.84%", msg)
            self.assertIn("EXIT_B", msg)

    def test_heartbeat_message(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            msg = build_heartbeat_message(conn)
            self.assertIn("System OK", msg)
            self.assertIn("Database", msg)

    def test_daily_digest(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            self._seed_event(conn)
            msg, day_key = build_daily_digest(conn)
            self.assertIn("DAILY REPORT", msg)
            self.assertRegex(day_key, r"\d{4}-\d{2}-\d{2}")

    def test_weekly_report(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            msg, week_key = build_weekly_report(conn)
            self.assertIn("WEEKLY REPORT", msg)
            self.assertRegex(week_key, r"\d{4}-W\d+")

    def test_opportunity_score_bounded(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            result = compute_opportunity_score(conn, event_id=eid)
            self.assertGreaterEqual(result["score"], 0)
            self.assertLessEqual(result["score"], 100)
            self.assertIn("components", result)

    def test_timeline_builder(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            conn.execute(
                """
                INSERT INTO market_event_context (
                  event_id, context_type, source, source_record_id,
                  context_ts, time_delta_seconds, relevance_score, context_json, created_at
                ) VALUES (?, 'TELEGRAM_SIGNAL', 'signalyp', '1', ?, -300, 0.9, '{}', ?)
                """,
                (eid, int(time.time()) - 300, int(time.time())),
            )
            timeline = build_event_timeline(conn, event_id=eid)
            stages = [s["stage"] for s in timeline]
            self.assertIn("Shock", stages)
            self.assertIn("Telegram", stages)
            text = format_timeline_text(timeline)
            self.assertIn("EVENT TIMELINE", text)

    def test_ai_comparison_verdict(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_event_analysis_jobs (
                  id, event_id, prompt_version, status, attempts, created_at
                ) VALUES (1, ?, 'v1', 'complete', 1, ?)
                """,
                (eid, now),
            )
            conn.execute(
                """
                INSERT INTO market_event_ai_analyses (
                  event_id, job_id, provider, model, prompt_version,
                  structured_output_json, movement_interpretation, reversal_bias,
                  confidence, context_ids_json, latency_ms, token_usage_json, created_at
                ) VALUES (?, 1, 'test', 'test', 'v1', ?, 'LIQUIDITY_SWEEP', 'FADE_FAVORED',
                  0.7, '[]', 1.0, '{}', ?)
                """,
                (eid, json.dumps({"reversal_bias": "FADE_FAVORED"}), now),
            )
            conn.execute(
                """
                INSERT INTO market_events_pending_shocks (
                  event_id, symbol, direction, detected_ts, shock_return_pct,
                  shock_extreme_price, monitor_until_ts, phase, confirmed_reversal,
                  confirm_latency_sec, created_at, updated_at
                ) VALUES (?, 'BTC', 'DOWN', ?, -3.0, 100, ?, 'CLOSED', 'R2', 240, ?, ?)
                """,
                (eid, int(time.time()), int(time.time()) + 900, int(time.time()), int(time.time())),
            )
            result = run_ai_comparison(conn, event_id=eid)
            self.assertIsNotNone(result)
            self.assertIn(result["verdict"], ("correct", "incorrect", "partial"))

    def test_dashboard_api_stats(self) -> None:
        from unittest.mock import patch
        from bot.research.market_events.alert_engine.dashboard_api import DashboardHandler
        from io import BytesIO

        class FakeHandler(DashboardHandler):
            def __init__(self):
                self.path = "/stats"
                self.wfile = BytesIO()
                self.response_code = None

            def send_response(self, code):
                self.response_code = code

            def send_header(self, key, value):
                pass

            def end_headers(self):
                pass

        with patch("bot.research.market_events.config.MARKET_EVENTS_DATABASE_PATH", self.db_path):
            handler = FakeHandler()
            handler.do_GET()
            body = json.loads(handler.wfile.getvalue().decode())
            self.assertEqual(handler.response_code, 200)
            self.assertIn("events_total", body)

    def test_format_shock_alert_delegates_v2(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            eid = self._seed_event(conn)
            msg = format_shock_alert(conn, eid)
            self.assertIn("🚨", msg)


if __name__ == "__main__":
    unittest.main()
