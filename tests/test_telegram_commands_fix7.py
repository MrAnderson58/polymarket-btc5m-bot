"""FIX-7 — Telegram command registration for S4 learning/paper commands."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    SUPPORTED_COMMANDS,
    handle_market_events_command,
    route_telegram_command,
)
from bot.research.market_events.sqlite_manager_g05 import PURE_READONLY_COMMANDS

_FIX7_COMMANDS = (
    "/review",
    "/paper",
    "/learning-status",
)

_FIX7_VARIANTS = (
    "/help",
    "/review",
    "/paper",
    "/paper today",
    "/paper week",
    "/learning-status",
)


class TestTelegramCommandsFix7(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "fix7.db"
        configure_unit_test_db_isolation(self.db_path)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_commands_in_supported_set(self) -> None:
        for cmd in _FIX7_COMMANDS:
            self.assertIn(cmd, SUPPORTED_COMMANDS, msg=cmd)

    def test_commands_in_pure_readonly(self) -> None:
        for cmd in _FIX7_COMMANDS:
            self.assertIn(cmd, PURE_READONLY_COMMANDS, msg=cmd)

    def test_help_lists_new_commands(self) -> None:
        result = handle_market_events_command("/help")
        self.assertTrue(result.ok)
        text = result.reply_text
        self.assertIn("/review", text)
        self.assertIn("/paper", text)
        self.assertIn("/paper today", text)
        self.assertIn("/paper week", text)
        self.assertIn("/learning-status", text)

    def test_variants_not_unknown(self) -> None:
        for text in _FIX7_VARIANTS:
            route = route_telegram_command(text)
            self.assertIsNotNone(route, msg=text)
            assert route is not None
            self.assertNotIn("Unknown command", route.reply_text, msg=text)
            self.assertTrue(route.ok, msg=f"{text}: {route.reply_text}")

    def test_review_and_learning_status_use_cli_handlers(self) -> None:
        review = handle_market_events_command("/review")
        self.assertTrue(review.ok)
        self.assertIn("signals (last 20)", review.reply_text)

        status = handle_market_events_command("/learning-status")
        self.assertTrue(status.ok)
        self.assertIn("Learning Pipeline", status.reply_text)

    def test_paper_variants_use_cli_handler(self) -> None:
        for text in ("/paper", "/paper today", "/paper week"):
            result = handle_market_events_command(text)
            self.assertTrue(result.ok, msg=result.reply_text)
            self.assertNotIn("Unknown command", result.reply_text)


if __name__ == "__main__":
    unittest.main()
