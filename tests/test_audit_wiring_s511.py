"""BUG-S5.1.1 — Audit Engine wiring regression tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    SUPPORTED_COMMANDS,
    _build_command_reply,
    route_telegram_command,
)


class TestAuditWiringS511(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "s511.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_cli_research_audit_registered(self) -> None:
        from io import StringIO
        import sys
        from bot.research.market_events.__main__ import main

        buf = StringIO()
        with patch.object(sys, "argv", ["x", "--help"]), patch("sys.stdout", buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        help_text = buf.getvalue()
        self.assertIn("research-audit", help_text)

    def test_telegram_router_knows_audit(self) -> None:
        self.assertIn("/audit", SUPPORTED_COMMANDS)

    def test_help_contains_audit_commands(self) -> None:
        help_text = _build_command_reply(None, "/help", [], chat_id=None)
        self.assertIn("/audit BTC", help_text)
        self.assertIn("/audit system", help_text)

    def test_route_audit_symbol_dispatches(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.audit_engine_s51.run_audit_s51_cli",
            return_value="AI AUDIT OK",
        ) as mock_run:
            result = route_telegram_command("/audit BTC")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.ok)
        self.assertEqual(result.reply_text, "AI AUDIT OK")
        mock_run.assert_called()
        self.assertEqual(str(mock_run.call_args.kwargs.get("symbol")).upper(), "BTC")

    def test_route_audit_system_dispatches(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.audit_engine_s51.run_audit_s51_cli",
            return_value="SYSTEM AUDIT OK",
        ) as mock_run:
            result = route_telegram_command("/audit system")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.ok)
        self.assertEqual(result.reply_text, "SYSTEM AUDIT OK")
        mock_run.assert_called()
        self.assertTrue(mock_run.call_args.kwargs.get("system") is True)

    def test_ai_audit_alias_dispatches(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.audit_engine_s51.run_audit_s51_cli",
            return_value="ALIAS AUDIT OK",
        ) as mock_run:
            result = route_telegram_command("/ai BTC --audit")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.ok)
        self.assertEqual(result.reply_text, "ALIAS AUDIT OK")
        mock_run.assert_called()
        self.assertEqual(str(mock_run.call_args.kwargs.get("symbol")).upper(), "BTC")

    def test_cli_positional_system_selects_system_mode(self) -> None:
        from bot.research.market_events.__main__ import main

        with patch(
            "bot.research.market_events.signal_intelligence.audit_engine_s51.run_audit_s51_cli",
            return_value="SYSTEM MODE",
        ) as mock_run:
            with patch(
                "bot.research.market_events.signal_intelligence.claude_channel_s50.telegram_claude_session",
            ):
                rc = main(["research-audit", "system"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()
        self.assertTrue(mock_run.call_args.kwargs.get("system") is True)

    def test_cli_symbol_flag_selects_symbol_mode(self) -> None:
        from bot.research.market_events.__main__ import main

        with patch(
            "bot.research.market_events.signal_intelligence.audit_engine_s51.run_audit_s51_cli",
            return_value="SYMBOL MODE",
        ) as mock_run:
            with patch(
                "bot.research.market_events.signal_intelligence.claude_channel_s50.telegram_claude_session",
            ):
                rc = main(["research-audit", "--symbol", "BTC"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()
        self.assertFalse(bool(mock_run.call_args.kwargs.get("system")))
        self.assertEqual(str(mock_run.call_args.kwargs.get("symbol")).upper(), "BTC")


if __name__ == "__main__":
    unittest.main()
