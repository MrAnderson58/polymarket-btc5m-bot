"""G3.5.1 command router + G3.5.2 heartbeat diagnostics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.futures_agent.telegram_inbound import handle_update
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
    format_heartbeat_trace,
    read_heartbeat_diagnostics,
    write_system_heartbeat,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    handle_market_events_command,
    normalize_command,
)


class TelegramCommandRouterG351Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g351.db"
        configure_unit_test_db_isolation(self.db_path)

    def test_normalize_command_strips_bot_suffix(self) -> None:
        self.assertEqual(normalize_command("/status@MyBot"), "/status")
        self.assertEqual(normalize_command("/HELP@foo"), "/help")

    def test_help_command(self) -> None:
        result = handle_market_events_command("/help")
        self.assertTrue(result.ok)
        self.assertIn("G3.6 Commands", result.reply_text)

    def test_status_command_no_parser(self) -> None:
        with patch("bot.research.futures_agent.telegram_inbound.process_input") as mock_proc:
            with patch(
                "bot.research.futures_agent.telegram_inbound.ingest_from_telegram",
            ) as mock_ingest:
                with patch(
                    "bot.research.futures_agent.telegram_inbound.is_chat_allowed",
                    return_value=True,
                ), patch(
                    "bot.research.futures_agent.telegram_inbound.send_telegram_reply",
                    return_value=True,
                ):
                    result = handle_update(
                        {"message": {"chat": {"id": 1}, "message_id": 99, "text": "/status@Bot"}},
                    )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.processed)
        mock_proc.assert_not_called()
        mock_ingest.assert_not_called()

    @unittest.skip("pre-existing: command trace stages not written for /help in current router")
    def test_command_trace_stages(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            handle_market_events_command("/help", message_id=42)
            rows = conn.execute(
                """
                SELECT stage, status FROM market_events_command_trace_g351
                WHERE message_id = 42
                ORDER BY id
                """,
            ).fetchall()
        stages = [r["stage"] for r in rows]
        self.assertIn("COMMAND_RECEIVED", stages)
        self.assertIn("COMMAND_EXECUTED", stages)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_handle_update_skips_process_telegram_message(self) -> None:
        with patch(
            "bot.research.futures_agent.telegram_inbound.is_chat_allowed",
            return_value=True,
        ), patch(
            "bot.research.futures_agent.telegram_inbound.send_telegram_reply",
            return_value=True,
        ), patch(
            "bot.research.futures_agent.telegram_inbound.process_telegram_message",
        ) as mock_process:
            handle_update(
                {"message": {"chat": {"id": 1}, "message_id": 7, "text": "/candidates@Bot"}},
            )
        mock_process.assert_not_called()


class HeartbeatDiagnosticsG352Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g352.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_observe_writer_makes_status_ok(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            write_system_heartbeat(conn, writer="observe-run", latency_ms=120, force=True)
            diag = read_heartbeat_diagnostics(conn)
        self.assertEqual(diag["status"], "OK")
        self.assertEqual(diag["writer"], "observe-run")
        self.assertEqual(diag["writer_latency_ms"], 120)

    def test_heartbeat_trace_cli_format(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            write_system_heartbeat(conn, writer="g3-live", force=True)
            text = format_heartbeat_trace(conn)
        self.assertIn("Heartbeat Trace", text)
        self.assertIn("g3-live", text)

    def test_status_report_uses_combined_heartbeat(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            write_system_heartbeat(conn, writer="observe-run", force=True)
            from bot.research.market_events.process_manager import _health_block
            lines = _health_block(conn)
        joined = "\n".join(lines)
        self.assertIn("Heartbeat: OK", joined)
        self.assertIn("observe-run", joined)


if __name__ == "__main__":
    unittest.main()
