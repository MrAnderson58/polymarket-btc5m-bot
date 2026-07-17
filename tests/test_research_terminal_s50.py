"""Tests for S5.0 AI Research Terminal."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
    claude_call_allowed,
    telegram_claude_session,
)
from bot.research.market_events.signal_intelligence.claude_client_g2 import ClaudeClientError
from bot.research.market_events.signal_intelligence.research_artifacts_s50 import (
    ARTIFACT_TEXT,
    ARTIFACT_URL,
    classify_url_domain,
    extract_urls,
    format_artifacts_report_s50,
    save_research_artifact_s50,
)
from bot.research.market_events.signal_intelligence.research_terminal_s50 import (
    dispatch_analyze_command_s50,
    format_cost_s50_cli,
    format_history_s50_cli,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    SUPPORTED_COMMANDS,
    route_telegram_command,
)


class TestResearchTerminalS50(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "s50.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_telegram_only_blocks_auto_claude(self) -> None:
        allowed, reason = claude_call_allowed()
        self.assertFalse(allowed)
        self.assertIn("telegram_only", reason or "")

    def test_telegram_session_allows_claude(self) -> None:
        with telegram_claude_session():
            allowed, _ = claude_call_allowed()
        self.assertTrue(allowed)

    @patch(
        "bot.research.market_events.signal_intelligence.claude_client_g2._post_messages",
        side_effect=RuntimeError("should not call in unit test"),
    )
    def test_call_claude_blocked_without_session(self, *_m: object) -> None:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import call_claude_g2

        with self.assertRaises(ClaudeClientError):
            call_claude_g2(system="s", user_content="u", label="test")

    def test_save_artifact_and_report(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            aid = save_research_artifact_s50(
                conn,
                artifact_type=ARTIFACT_TEXT,
                symbol="BTC",
                content_text="ETF flows rising",
                telegram_user="tester",
            )
            conn.commit()
            self.assertGreater(aid, 0)
            text = format_artifacts_report_s50(conn, symbol="BTC")
        self.assertIn("Research Artifacts", text)
        self.assertIn("TEXT", text)

    def test_url_helpers(self) -> None:
        urls = extract_urls("Check https://x.com/foo/status/1 and go")
        self.assertEqual(urls[0], "https://x.com/foo/status/1")
        self.assertEqual(classify_url_domain("https://www.coindesk.com/x"), "coindesk.com")

    def test_cost_and_history_empty(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        self.assertIn("Claude Budget", format_cost_s50_cli())
        self.assertIn("No requests yet", format_history_s50_cli())

    def test_dispatch_analyze_url(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.research_terminal_s50.run_analyze_url_s50_cli",
            return_value="url analysis",
        ) as mock:
            out = dispatch_analyze_command_s50(["https://github.com/foo/bar"])
        self.assertEqual(out, "url analysis")
        mock.assert_called_once()

    def test_telegram_commands_registered(self) -> None:
        for cmd in ("/ai", "/compare", "/cost", "/history", "/artifacts"):
            self.assertIn(cmd, SUPPORTED_COMMANDS)

    def test_route_cost_readonly(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        route = route_telegram_command("/cost")
        self.assertIsNotNone(route)
        self.assertIn("Claude Budget", route.reply_text)


if __name__ == "__main__":
    unittest.main()
