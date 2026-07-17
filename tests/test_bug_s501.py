"""BUG-S5.0.1 — CLI UnboundLocalError, local review drain, health/telegram commands."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
    MAX_LOCAL_REVIEWS_PER_CYCLE,
    REVIEW_STATUS_COMPLETE,
    REVIEW_TYPE_LOCAL,
    _generate_review_text_s40,
    run_learning_reviews_s40_once,
)


class TestBugS501(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "s501.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_cli_readonly_connection_not_unbound(self) -> None:
        """Regression: local import of market_events_readonly_connection broke CLI."""
        import bot.research.market_events.__main__ as main_mod

        self.assertTrue(callable(main_mod.market_events_readonly_connection))
        # Simulate the name resolution that previously raised UnboundLocalError.
        fn = main_mod.main.__code__
        self.assertNotIn(
            "market_events_readonly_connection",
            fn.co_varnames,
            "local import of market_events_readonly_connection makes it UnboundLocal in main()",
        )

    def test_local_review_when_claude_blocked(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.signal_learning_s40.is_claude_configured",
            return_value=True,
        ), patch(
            "bot.research.market_events.signal_intelligence.signal_learning_s40.claude_call_allowed",
            return_value=(False, "telegram_only"),
        ):
            text, _o, status, rtype, timed_out, _reason = _generate_review_text_s40(
                signal_type="g3_signal",
                signal_row={"symbol": "BTC", "direction": "LONG", "pnl_pct": 1.0},
                snapshot={},
                checkpoints=[],
            )
        self.assertFalse(timed_out)
        self.assertEqual(status, REVIEW_STATUS_COMPLETE)
        self.assertEqual(rtype, REVIEW_TYPE_LOCAL)
        self.assertIn("Local Review", text)

    def test_local_mode_raises_cycle_budget(self) -> None:
        self.assertEqual(MAX_LOCAL_REVIEWS_PER_CYCLE, 100)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        with patch(
            "bot.research.market_events.signal_intelligence.signal_learning_s40.claude_call_allowed",
            return_value=(False, "telegram_only"),
        ), patch(
            "bot.research.market_events.signal_intelligence.signal_learning_s40.is_claude_configured",
            return_value=True,
        ):
            stats = run_learning_reviews_s40_once(limit=5)
        self.assertEqual(stats.get("mode"), "local")

    def test_health_and_telegram_status_cli_registered(self) -> None:
        from bot.research.market_events.__main__ import main
        import sys
        from io import StringIO
        from unittest.mock import patch as _patch

        # --help lists new commands
        buf = StringIO()
        with _patch.object(sys, "argv", ["market_events", "--help"]), _patch("sys.stdout", buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        help_text = buf.getvalue()
        self.assertIn("health", help_text)
        self.assertIn("telegram-status", help_text)
        self.assertIn("restart-telegram", help_text)

    def test_system_health_report_smoke(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        from bot.research.market_events.process_manager import system_health_report
        text = system_health_report()
        self.assertIn("MARKET EVENTS HEALTH", text)
        self.assertIn("Overall:", text)
        self.assertIn("Claude:", text)

    def test_telegram_status_report_smoke(self) -> None:
        from bot.research.market_events.process_manager import telegram_status_report
        text = telegram_status_report()
        self.assertIn("Telegram", text)
        self.assertIn("Running:", text)
        self.assertIn("Mode: polling", text)


if __name__ == "__main__":
    unittest.main()
