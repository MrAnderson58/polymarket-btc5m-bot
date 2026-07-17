"""Tests for S5.1 AI Audit Engine."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.audit_engine_s51 import (
    _build_symbol_audit_context_s51,
    dispatch_audit_command_s51,
    run_audit_s51_cli,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    SUPPORTED_COMMANDS,
)


class TestAuditEngineS51(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "s51.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_audit_command_registered(self) -> None:
        self.assertIn("/audit", SUPPORTED_COMMANDS)

    def test_build_symbol_context_has_required_sections(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
            ctx, _ids = _build_symbol_audit_context_s51(conn, symbol="BTC")
        for token in (
            "Decision",
            "Market Agent",
            "News",
            "Pattern",
            "Paper",
            "Learning",
            "Similar",
            "Artifacts",
        ):
            self.assertIn(token, ctx)

    def test_dispatch_usage(self) -> None:
        self.assertIn("Usage", dispatch_audit_command_s51([]))

    def test_run_audit_without_claude_key(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        with patch(
            "bot.research.market_events.signal_intelligence.research_terminal_s50.is_claude_configured",
            return_value=False,
        ):
            text = run_audit_s51_cli(symbol="BTC")
        self.assertIn("not configured", text.lower())

    def test_cli_help_lists_research_audit(self) -> None:
        import sys
        from io import StringIO
        from bot.research.market_events.__main__ import main

        buf = StringIO()
        with patch.object(sys, "argv", ["x", "--help"]), patch("sys.stdout", buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        self.assertIn("research-audit", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
