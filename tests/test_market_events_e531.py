"""Phase E.5.3.1 Telegram configuration and delivery reliability tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.alert_config import resolve_alert_chat_id
from bot.research.market_events.alert_engine.telegram_delivery import (
    TelegramDeliveryResult,
    log_delivery_attempt,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.telegram_ops.cli import run_demo_event
from bot.research.market_events.telegram_ops.config_report import format_telegram_config_report
from bot.research.market_events.telegram_ops.retry import retry_failed_deliveries
from bot.research.market_events.telegram_ops.startup_validation import validate_telegram_config_at_startup


class MarketEventsE531Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e531.db"
        self._env: dict[str, str | None] = {}
        self._env_patch = patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _set_env(self, **kwargs: str | None) -> None:
        for key, value in kwargs.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v11_message_text(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v11", applied)
            self.assertIn("v18", applied)
            self.assertEqual(SCHEMA_VERSION, 41)
            cols = {
                r[1] for r in conn.execute(
                    "PRAGMA table_info(market_event_telegram_delivery_log)",
                ).fetchall()
            }
            self.assertIn("message_text", cols)

    def test_single_allowed_chat_auto_resolution(self) -> None:
        self._set_env(
            ME_ALERT_CHAT_ID=None,
            TELEGRAM_AGENT_CHAT_ID=None,
            TELEGRAM_AGENT_ALLOWED_CHAT_IDS="517492533",
        )
        resolution = resolve_alert_chat_id()
        self.assertEqual(resolution.chat_id, "517492533")
        self.assertEqual(resolution.source, "TELEGRAM_AGENT_ALLOWED_CHAT_IDS")
        self.assertIsNone(resolution.error)

    def test_me_alert_chat_id_precedence(self) -> None:
        self._set_env(
            ME_ALERT_CHAT_ID="111111",
            TELEGRAM_AGENT_CHAT_ID="222222",
            TELEGRAM_AGENT_ALLOWED_CHAT_IDS="333333",
        )
        resolution = resolve_alert_chat_id()
        self.assertEqual(resolution.chat_id, "111111")
        self.assertEqual(resolution.source, "ME_ALERT_CHAT_ID")

    def test_agent_chat_id_second_priority(self) -> None:
        self._set_env(
            ME_ALERT_CHAT_ID=None,
            TELEGRAM_AGENT_CHAT_ID="222222",
            TELEGRAM_AGENT_ALLOWED_CHAT_IDS="333333,444444",
        )
        resolution = resolve_alert_chat_id()
        self.assertEqual(resolution.chat_id, "222222")
        self.assertEqual(resolution.source, "TELEGRAM_AGENT_CHAT_ID")

    def test_multiple_allowed_ids_require_explicit_chat(self) -> None:
        self._set_env(
            ME_ALERT_CHAT_ID=None,
            TELEGRAM_AGENT_CHAT_ID=None,
            TELEGRAM_AGENT_ALLOWED_CHAT_IDS="111111,222222",
        )
        resolution = resolve_alert_chat_id()
        self.assertIsNone(resolution.chat_id)
        self.assertIn("ME_ALERT_CHAT_ID", resolution.error or "")

    @patch("bot.research.market_events.telegram_ops.retry.deliver_telegram")
    def test_retry_failed_deliveries(self, mock_deliver) -> None:
        mock_deliver.return_value = TelegramDeliveryResult(
            ok=True, error=None, latency_ms=50.0,
            http_code=200, message_id=99, attempts=1, chat_id="123",
        )
        with self._conn() as conn:
            apply_migrations(conn)
            log_delivery_attempt(
                conn, event_id=1, alert_type="SHOCK_DETECTED", chat_id="123",
                message_text="full retry body", status="failed", latency_ms=10.0,
                http_code=503, message_id=None, attempt=1, error="http_503",
            )
            lines = retry_failed_deliveries(conn, limit=100)
            text = "\n".join(lines)
            self.assertIn("sent", text)
            mock_deliver.assert_called_once()
            call_kwargs = mock_deliver.call_args.kwargs
            self.assertEqual(call_kwargs["event_id"], 1)

    def test_telegram_config_output(self) -> None:
        self._set_env(
            TELEGRAM_BOT_TOKEN="test-token",
            ME_ALERT_CHAT_ID="12345",
        )
        with patch(
            "bot.research.market_events.telegram_ops.config_report.fetch_bot_info",
            return_value={"id": 1, "username": "testbot"},
        ):
            report = format_telegram_config_report()
        self.assertIn("Bot Token:", report)
        self.assertIn("configured", report)
        self.assertIn("12345", report)
        self.assertIn("ME_ALERT_CHAT_ID", report)
        self.assertIn("reachable yes", report)

    def _fake_deliver(self, text, *, conn=None, alert_type="UNKNOWN", event_id=0, **kwargs):
        log_delivery_attempt(
            conn, event_id=event_id, alert_type=alert_type, chat_id="123",
            message_text=text, status="sent", latency_ms=1.0,
            http_code=200, message_id=1, attempt=1,
        )
        return TelegramDeliveryResult(
            ok=True, error=None, latency_ms=1.0, http_code=200,
            message_id=1, attempts=1, chat_id="123",
        )

    @patch("bot.research.market_events.alert_engine.telegram_delivery.deliver_telegram")
    def test_demo_event_reports_sent_on_http_200(self, mock_deliver) -> None:
        mock_deliver.side_effect = self._fake_deliver
        self._set_env(ME_AI_ANALYST_ENABLED="true", ME_AI_PROVIDER="deterministic")
        with self._conn() as conn:
            apply_migrations(conn)
            code, text = run_demo_event(conn)
            self.assertIn("2. Telegram shock alert       sent", text)
            self.assertEqual(code, 0)

    def test_demo_event_reports_failed_without_delivery(self) -> None:
        self._set_env(
            ME_AI_ANALYST_ENABLED="true",
            ME_AI_PROVIDER="deterministic",
            TELEGRAM_BOT_TOKEN="fake-token",
            ME_ALERT_CHAT_ID=None,
            TELEGRAM_AGENT_CHAT_ID=None,
            TELEGRAM_AGENT_ALLOWED_CHAT_IDS="111111,222222",
        )
        with self._conn() as conn:
            apply_migrations(conn)
            code, text = run_demo_event(conn)
            self.assertIn("failed (Multiple TELEGRAM_AGENT_ALLOWED_CHAT_IDS", text)
            self.assertEqual(code, 1)

    @patch("bot.research.futures_agent.telegram_config.get_telegram_bot_token", return_value="tok")
    @patch("bot.research.market_events.alert_config.resolve_alert_chat_id")
    def test_startup_validation_warns_on_unresolved_chat(self, mock_resolve, _mock_token) -> None:
        from bot.research.market_events.alert_config import ChatIdResolution

        mock_resolve.return_value = ChatIdResolution(
            None, None, "Multiple TELEGRAM_AGENT_ALLOWED_CHAT_IDS configured; set ME_ALERT_CHAT_ID explicitly",
        )
        with self.assertLogs("bot.research.market_events.telegram_ops.startup_validation", level="WARNING") as logs:
            validate_telegram_config_at_startup()
        self.assertTrue(any("TELEGRAM CONFIG WARNING" in msg for msg in logs.output))


if __name__ == "__main__":
    unittest.main()
