"""Phase E.5.3 Telegram ops and end-to-end validation tests (offline)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from bot.research.market_events.alert_engine.telegram_delivery import (
    RETRYABLE_HTTP_CODES,
    TelegramDeliveryResult,
    _backoff_delay,
    _is_retryable,
    deliver_telegram,
    log_delivery_attempt,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.telegram_ops.cli import (
    build_test_message,
    run_ai_test,
    run_demo_event,
    run_telegram_alert_test,
    run_telegram_health,
)
from bot.research.market_events.telegram_ops.synthetic_event import create_synthetic_shock_event


class MarketEventsE53Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e53.db"
        self._env_patch = patch.dict(os.environ, {
            "ME_TELEGRAM_ALERTS_ENABLED": "true",
            "ME_AI_ANALYST_ENABLED": "true",
            "ME_AI_PROVIDER": "deterministic",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "ME_ALERT_CHAT_ID": "-1001234567890",
        }, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v10_migration(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v11", applied)
            self.assertIn("v18", applied)
            self.assertEqual(SCHEMA_VERSION, 28)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_event_telegram_delivery_log", tables)

    def test_retryable_codes_and_backoff(self) -> None:
        self.assertIn(429, RETRYABLE_HTTP_CODES)
        self.assertIn(503, RETRYABLE_HTTP_CODES)
        self.assertTrue(_is_retryable(429, None))
        self.assertTrue(_is_retryable(None, requests.Timeout()))
        self.assertFalse(_is_retryable(400, None))
        self.assertGreater(_backoff_delay(1), _backoff_delay(0))

    def test_delivery_log_persist(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            log_delivery_attempt(
                conn, event_id=0, alert_type="TEST", chat_id="123",
                message_text="hello", status="sent", latency_ms=42.0,
                http_code=200, message_id=999, attempt=1,
            )
            row = conn.execute(
                "SELECT * FROM market_event_telegram_delivery_log WHERE alert_type = 'TEST'",
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["telegram_message_id"], 999)
            self.assertEqual(row["http_code"], 200)

    @patch("bot.research.market_events.alert_engine.telegram_delivery._post_telegram")
    def test_deliver_telegram_success(self, mock_post) -> None:
        mock_post.return_value = (200, {"ok": True, "result": {"message_id": 42}}, None)
        with self._conn() as conn:
            apply_migrations(conn)
            result = deliver_telegram("test msg", conn=conn, alert_type="TEST")
            self.assertTrue(result.ok)
            self.assertEqual(result.message_id, 42)
            self.assertEqual(result.http_code, 200)
            count = conn.execute(
                "SELECT COUNT(*) FROM market_event_telegram_delivery_log WHERE status = 'sent'",
            ).fetchone()[0]
            self.assertEqual(count, 1)

    @patch("bot.research.market_events.alert_engine.telegram_delivery.time.sleep")
    @patch("bot.research.market_events.alert_engine.telegram_delivery._post_telegram")
    def test_deliver_telegram_retries_on_503(self, mock_post, mock_sleep) -> None:
        mock_post.side_effect = [
            (503, {"ok": False}, None),
            (200, {"ok": True, "result": {"message_id": 7}}, None),
        ]
        with self._conn() as conn:
            apply_migrations(conn)
            result = deliver_telegram("retry msg", conn=conn, alert_type="TEST", max_retries=2)
            self.assertTrue(result.ok)
            self.assertEqual(result.attempts, 2)
            self.assertEqual(mock_post.call_count, 2)
            mock_sleep.assert_called_once()
            attempts = conn.execute(
                "SELECT COUNT(*) FROM market_event_telegram_delivery_log",
            ).fetchone()[0]
            self.assertEqual(attempts, 2)

    @patch("bot.research.market_events.alert_engine.telegram_delivery.deliver_telegram")
    def test_telegram_alert_test_offline(self, mock_deliver) -> None:
        mock_deliver.return_value = TelegramDeliveryResult(
            ok=True, error=None, latency_ms=320.0,
            http_code=200, message_id=1001, attempts=1,
            chat_id="-1001234567890",
        )
        with self._conn() as conn:
            apply_migrations(conn)
            code, text = run_telegram_alert_test(conn)
            self.assertEqual(code, 0)
            self.assertIn("MARKET EVENTS TEST", text)
            self.assertIn("320 ms", text)
            self.assertIn("1001", text)
            mock_deliver.assert_called_once()

    def test_build_test_message(self) -> None:
        msg = build_test_message(db_ok=True)
        self.assertIn("🧪 MARKET EVENTS TEST", msg)
        self.assertIn("Database: OK", msg)
        self.assertIn("PAPER", msg)

    def test_ai_test_offline(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            code, text = run_ai_test(conn, send_telegram=False)
            self.assertEqual(code, 0)
            self.assertIn("interpretation:", text)
            self.assertIn("status: OK", text)

    def test_demo_event_offline(self) -> None:
        def _fake_deliver(text, *, conn=None, alert_type="UNKNOWN", event_id=0, **kwargs):
            from bot.research.market_events.alert_engine.telegram_delivery import (
                TelegramDeliveryResult,
                log_delivery_attempt,
            )
            log_delivery_attempt(
                conn, event_id=event_id, alert_type=alert_type, chat_id="123",
                message_text=text, status="sent", latency_ms=1.0,
                http_code=200, message_id=1, attempt=1,
            )
            return TelegramDeliveryResult(
                ok=True, error=None, latency_ms=1.0, http_code=200,
                message_id=1, attempts=1, chat_id="123",
            )

        with patch(
            "bot.research.market_events.alert_engine.telegram_delivery.deliver_telegram",
            side_effect=_fake_deliver,
        ):
            with self._conn() as conn:
                apply_migrations(conn)
                code, text = run_demo_event(conn)
                self.assertEqual(code, 0)
                self.assertIn("Pipeline: OK", text)
                self.assertIn("Telegram shock alert       sent", text)

    def test_telegram_health_report(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            create_synthetic_shock_event(conn)
            log_delivery_attempt(
                conn, event_id=0, alert_type="TEST", chat_id="123",
                message_text="x", status="sent", latency_ms=100.0,
                http_code=200, message_id=1, attempt=1,
            )
            report = run_telegram_health(conn)
            self.assertIn("Bot Token:", report)
            self.assertIn("AI queue", report)
            self.assertIn("Retry queue", report)

    @patch("bot.research.market_events.alert_engine.telegram_delivery.deliver_telegram")
    def test_telegram_alert_test_fails_without_delivery(self, mock_deliver) -> None:
        mock_deliver.return_value = TelegramDeliveryResult(
            ok=False, error="no_token", latency_ms=0.0,
            http_code=None, message_id=None, attempts=0, chat_id=None,
        )
        with self._conn() as conn:
            apply_migrations(conn)
            code, _ = run_telegram_alert_test(conn)
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
