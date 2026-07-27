"""Tests for market_events doctor CLI."""

from __future__ import annotations

import unittest
from io import StringIO
from unittest.mock import patch

from bot.research.market_events.doctor import (
    Check,
    collect_doctor,
    format_doctor,
    run_doctor,
)


class DoctorUnitTests(unittest.TestCase):
    def test_format_matches_ops_layout(self) -> None:
        data = {
            "ok": True,
            "sha": "abc1234",
            "db": [Check("SQLite", True), Check("PostgreSQL", True)],
            "market": [
                Check("Yahoo", True),
                Check("Binance", True),
                Check("Farside", True),
            ],
            "news": [
                Check("RSS", True),
                Check("Telegram", True),
                Check("X", True),
            ],
            "ai": [Check("Claude", True), Check("OpenAI", True)],
            "telegram": Check("Connected", True),
            "paper": Check("Running", True),
            "open_trades": 2,
            "signals_today": 14,
            "last_report": "15:42 UTC",
        }
        text = format_doctor(data)
        self.assertIn("AI Trading Platform", text)
        self.assertIn("Git SHA:\nabc1234", text)
        self.assertIn("✓ SQLite", text)
        self.assertIn("✓ Yahoo", text)
        self.assertIn("✓ RSS", text)
        self.assertIn("✓ Claude", text)
        self.assertIn("✓ Connected", text)
        self.assertIn("✓ Running", text)
        self.assertIn("Open Trades:\n2", text)
        self.assertIn("Signals today:\n14", text)
        self.assertIn("Last Report:\n15:42 UTC", text)
        self.assertIn("Everything OK", text)

    def test_run_doctor_skip_network_smoke(self) -> None:
        with patch(
            "bot.research.market_events.runtime_health._check_polymarket",
            return_value=(True, "ok"),
        ), patch(
            "bot.research.market_events.runtime_health._check_bybit",
            return_value=(True, "ok"),
        ), patch(
            "bot.research.market_events.runtime_health._proc_ok",
            return_value=(True, "PID 1"),
        ), patch(
            "bot.research.market_events.runtime_health._sqlite_lock_recent",
            return_value=(True, "none"),
        ), patch(
            "bot.research.market_events.runtime_health._recent_errors",
            return_value=[],
        ), patch(
            "bot.research.market_events.runtime_health._db_size_bytes",
            return_value=1000,
        ):
            with patch(
                "bot.research.market_events.db.market_events_readonly_connection",
            ) as ro:
                from contextlib import contextmanager

                @contextmanager
                def _fake():
                    import sqlite3

                    con = sqlite3.connect(":memory:")
                    con.row_factory = sqlite3.Row
                    yield con
                    con.close()

                ro.side_effect = _fake
                text = run_doctor(skip_network=True)
        self.assertIn("SYSTEM STATUS", text)
        self.assertIn("Overall:", text)

    def test_collect_doctor_dict_keys(self) -> None:
        with patch(
            "bot.research.market_events.doctor._check_telegram_bot",
            return_value=Check("Connected", True),
        ), patch(
            "bot.research.market_events.doctor._check_sqlite",
            return_value=Check("SQLite", True),
        ), patch(
            "bot.research.market_events.doctor._check_postgres",
            return_value=Check("PostgreSQL", True),
        ), patch(
            "bot.research.market_events.doctor._check_news",
            return_value=[
                Check("RSS", False),
                Check("Telegram", False),
                Check("X", False),
            ],
        ), patch(
            "bot.research.market_events.doctor._check_ai",
            return_value=[Check("Claude", False), Check("OpenAI", False)],
        ), patch(
            "bot.research.market_events.doctor._check_paper_trading",
            return_value=(Check("Running", False), 3),
        ), patch(
            "bot.research.market_events.doctor._signals_today",
            return_value=7,
        ), patch(
            "bot.research.market_events.doctor._last_report_utc",
            return_value="12:00 UTC",
        ), patch(
            "bot.research.market_events.doctor._git_sha",
            return_value="cafe123",
        ):
            data = collect_doctor(skip_network=True)
        self.assertTrue(data["ok"])
        self.assertEqual(data["open_trades"], 3)
        self.assertEqual(data["signals_today"], 7)
        self.assertEqual(data["sha"], "cafe123")


class DoctorCliTests(unittest.TestCase):
    def test_doctor_registered_in_help(self) -> None:
        from bot.research.market_events.__main__ import main
        import sys

        buf = StringIO()
        with patch.object(sys, "argv", ["market_events", "--help"]), patch("sys.stdout", buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        help_text = buf.getvalue()
        self.assertIn("doctor", help_text)

    def test_doctor_cli_runs(self) -> None:
        from bot.research.market_events.__main__ import main

        with patch(
            "bot.research.market_events.runtime_health.run_runtime_doctor",
            return_value="SYSTEM STATUS\nOverall: HEALTHY",
        ) as mock_run:
            code = main(["doctor", "--skip-network"])
        self.assertEqual(code, 0)
        mock_run.assert_called_once_with(skip_network=True)


if __name__ == "__main__":
    unittest.main()
