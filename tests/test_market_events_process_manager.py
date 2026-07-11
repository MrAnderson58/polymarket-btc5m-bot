"""Process supervisor start-all / stop-all / status tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.market_events.process_manager import (
    ManagedService,
    SERVICES,
    find_service_processes,
    start_service,
    status_report,
    stop_service,
)


class ProcessManagerTests(unittest.TestCase):
    def test_services_defined(self) -> None:
        keys = {s.key for s in SERVICES}
        self.assertIn("shock-paper-core", keys)
        self.assertIn("dashboard", keys)
        self.assertEqual(len(SERVICES), 5)

    @patch("bot.research.market_events.process_manager._ps_rows")
    def test_find_by_ps_markers(self, mock_ps) -> None:
        svc = SERVICES[0]
        mock_ps.return_value = [
            (1234, "python -m bot.research.market_events shock-paper-run --universe core --paper-only"),
        ]
        found = find_service_processes(svc)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].pid, 1234)

    @patch("bot.research.market_events.process_manager.subprocess.Popen")
    @patch("bot.research.market_events.process_manager.find_service_processes", return_value=[])
    @patch("bot.research.market_events.process_manager.logs_dir")
    @patch("bot.research.market_events.process_manager.PID_DIR")
    def test_start_service_writes_pid(self, mock_pid_dir, mock_logs, mock_find, mock_popen) -> None:
        tmp = Path(tempfile.mkdtemp())
        mock_pid_dir.mkdir = MagicMock()
        mock_pid_dir.__truediv__ = lambda self, x: tmp / f"{x}"
        mock_logs.return_value = tmp
        proc = MagicMock()
        proc.pid = 9999
        mock_popen.return_value = proc
        svc = SERVICES[0]
        ok, msg = start_service(svc)
        self.assertTrue(ok)
        self.assertIn("9999", msg)
        mock_popen.assert_called_once()
        env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env")
        self.assertEqual(env.get("ME_AI_EMBEDDED_IN_PAPER_RUN"), "false")

    @patch("bot.research.market_events.process_manager.stop_processes", return_value=(True, ["ai: stopped"]))
    @patch("bot.research.market_events.process_manager.find_service_processes")
    def test_stop_service(self, mock_find, mock_stop) -> None:
        svc = next(s for s in SERVICES if s.key == "ai-worker")
        mock_find.return_value = [MagicMock(pid=42, command="test")]
        ok, msg = stop_service(svc)
        self.assertTrue(ok)
        mock_stop.assert_called_once()

    @patch("bot.research.market_events.process_manager.find_service_processes", return_value=[])
    @patch("bot.research.futures_agent.env_bootstrap.bootstrap_config", MagicMock())
    @patch("bot.ops.process_utils.find_telegram_poll_processes", return_value=[])
    def test_status_report_format(self, mock_tg, mock_find) -> None:
        import os
        import tempfile
        from bot.research.market_events.db import market_events_connection
        from bot.research.market_events.event_schema import apply_migrations

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "status.db"
            with patch("bot.research.market_events.config.MARKET_EVENTS_DATABASE_PATH", db):
                with market_events_connection(db_path=db) as conn:
                    apply_migrations(conn)
                text = status_report()
                self.assertIn("MARKET EVENTS STATUS", text)
                self.assertIn("shock-paper core", text)
                self.assertIn("telegram", text)
                self.assertIn("DB:", text)


if __name__ == "__main__":
    unittest.main()
