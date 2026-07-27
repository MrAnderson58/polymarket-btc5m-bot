"""Tests for runtime health doctor / watch / self-test."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.runtime_health import (
    RuntimeHealth,
    RuntimeLine,
    collect_runtime_health,
    format_runtime_health,
    run_self_test,
)


class TestRuntimeHealth(unittest.TestCase):
    def test_format_system_status(self) -> None:
        rh = RuntimeHealth(
            lines=[
                RuntimeLine("Core", "OK"),
                RuntimeLine("TradFi", "OK"),
                RuntimeLine("Bybit", "OK"),
                RuntimeLine("Polymarket", "OK"),
                RuntimeLine("SQLite", "OK"),
                RuntimeLine("Heartbeat", "OK", "22 sec ago"),
                RuntimeLine("Events", "OK"),
                RuntimeLine("Near miss", "OK"),
                RuntimeLine("Shadow", "OK"),
            ],
            heartbeat_age_sec=22,
            db_bytes=150_000_000,
        )
        text = format_runtime_health(rh)
        self.assertIn("SYSTEM STATUS", text)
        self.assertIn("Core", text)
        self.assertIn("Overall: HEALTHY", text)
        self.assertIn("22 sec ago", text)

    def test_warning_reasons(self) -> None:
        rh = RuntimeHealth(
            lines=[RuntimeLine("Heartbeat", "WARN", "120 sec ago")],
            reasons=["Heartbeat stale"],
            heartbeat_age_sec=120,
        )
        text = format_runtime_health(rh)
        self.assertIn("Overall: WARNING", text)
        self.assertIn("Heartbeat stale", text)

    @patch("bot.research.market_events.runtime_health._check_polymarket", return_value=(True, "ok"))
    @patch("bot.research.market_events.runtime_health._check_bybit", return_value=(True, "ok"))
    @patch("bot.research.market_events.runtime_health._proc_ok", return_value=(True, "PID 1"))
    @patch("bot.research.market_events.runtime_health._sqlite_lock_recent", return_value=(True, "none"))
    @patch("bot.research.market_events.runtime_health._recent_errors", return_value=[])
    def test_collect_smoke(
        self,
        _err,
        _lock,
        _proc,
        _bybit,
        _poly,
    ) -> None:
        from bot.research.market_events.db import market_events_connection
        from bot.research.market_events.db_config import configure_unit_test_db_isolation
        from bot.research.market_events.event_schema import apply_migrations

        with tempfile.TemporaryDirectory() as tmp:
            configure_unit_test_db_isolation(Path(tmp) / "t.db")
            with market_events_connection() as conn:
                apply_migrations(conn)
                from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
                    write_system_heartbeat,
                )

                write_system_heartbeat(conn, writer="test")
                conn.commit()
            rh = collect_runtime_health(skip_network=True)
        self.assertIsNotNone(rh.heartbeat_age_sec)

    def test_self_test_skip_network(self) -> None:
        text = run_self_test(skip_network=True)
        self.assertEqual(text.strip().splitlines()[0], "PASS")

    def test_cli_registered(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
        )
        out = proc.stdout + proc.stderr
        self.assertIn("watch", out)
        self.assertIn("self-test", out)


if __name__ == "__main__":
    unittest.main()
