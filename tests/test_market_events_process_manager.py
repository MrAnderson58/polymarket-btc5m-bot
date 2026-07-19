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
        self.assertIn("telegram", keys)
        self.assertEqual(len(SERVICES), 9)
        self.assertIn("g3-live", keys)
        self.assertIn("learning", keys)
        self.assertIn("narrative-engine", keys)
        tg = next(s for s in SERVICES if s.key == "telegram")
        self.assertEqual(tg.label, "telegram")
        self.assertIn("telegram-poll", tg.module_args)
        ne = next(s for s in SERVICES if s.key == "narrative-engine")
        self.assertEqual(ne.log_name, "narrative-engine.log")
        self.assertIn("narrative-engine-run", ne.module_args)

    @patch("bot.research.market_events.process_manager._ps_rows")
    def test_find_by_ps_markers(self, mock_ps) -> None:
        svc = SERVICES[0]
        mock_ps.return_value = [
            (1234, "python -m bot.research.market_events shock-paper-run --universe core --paper-only"),
        ]
        found = find_service_processes(svc)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].pid, 1234)

    @patch("bot.research.market_events.process_manager.find_telegram_poll_processes")
    def test_find_telegram_markers(self, mock_tg) -> None:
        from bot.ops.process_utils import ProcessInfo

        tg = next(s for s in SERVICES if s.key == "telegram")
        mock_tg.return_value = [
            ProcessInfo(
                pid=5555,
                command="python -m bot.research.futures_agent telegram-poll",
            ),
        ]
        found = find_service_processes(tg)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].pid, 5555)

    @patch("bot.research.market_events.process_manager.time.sleep", MagicMock())
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
        proc.poll.return_value = None
        mock_popen.return_value = proc
        svc = SERVICES[0]
        ok, msg = start_service(svc)
        self.assertTrue(ok)
        self.assertIn("9999", msg)
        mock_popen.assert_called_once()
        env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env")
        self.assertEqual(env.get("ME_AI_EMBEDDED_IN_PAPER_RUN"), "false")

    @patch("bot.research.market_events.process_manager.time.sleep", MagicMock())
    @patch("bot.research.market_events.process_manager.remove_stale_telegram_lock", return_value=(True, "no lock file present"))
    @patch("bot.research.futures_agent.env_bootstrap.bootstrap_config", MagicMock())
    @patch("bot.research.market_events.process_manager.subprocess.Popen")
    @patch("bot.research.market_events.process_manager.find_service_processes", return_value=[])
    @patch("bot.research.market_events.process_manager.logs_dir")
    @patch("bot.research.market_events.process_manager.PID_DIR")
    def test_start_telegram_uses_futures_agent(
        self, mock_pid_dir, mock_logs, mock_find, mock_popen, mock_lock,
    ) -> None:
        tmp = Path(tempfile.mkdtemp())
        mock_pid_dir.mkdir = MagicMock()
        mock_pid_dir.__truediv__ = lambda self, x: tmp / f"{x}"
        mock_logs.return_value = tmp
        proc = MagicMock()
        proc.pid = 7777
        proc.poll.return_value = None
        mock_popen.return_value = proc
        tg = next(s for s in SERVICES if s.key == "telegram")
        ok, msg = start_service(tg)
        self.assertTrue(ok)
        self.assertIn("7777", msg)
        cmd = mock_popen.call_args.args[0]
        self.assertIn("bot.research.futures_agent", cmd)
        self.assertIn("telegram-poll", cmd)
        mock_lock.assert_called()

    @patch("bot.research.market_events.process_manager.time.sleep", MagicMock())
    @patch("bot.research.market_events.process_manager.remove_stale_telegram_lock", return_value=(True, "no lock file present"))
    @patch("bot.research.futures_agent.env_bootstrap.bootstrap_config", MagicMock())
    @patch("bot.research.market_events.process_manager.subprocess.Popen")
    @patch("bot.research.market_events.process_manager.find_service_processes", return_value=[])
    @patch("bot.research.market_events.process_manager.logs_dir")
    @patch("bot.research.market_events.process_manager.PID_DIR")
    def test_start_telegram_reports_immediate_exit(
        self, mock_pid_dir, mock_logs, mock_find, mock_popen, mock_lock,
    ) -> None:
        tmp = Path(tempfile.mkdtemp())
        mock_pid_dir.mkdir = MagicMock()
        mock_pid_dir.__truediv__ = lambda self, x: tmp / f"{x}"
        mock_logs.return_value = tmp
        log_file = tmp / "me-telegram.log"
        log_file.write_text("ERROR: TELEGRAM_BOT_TOKEN is required\n")
        proc = MagicMock()
        proc.pid = 8888
        proc.poll.return_value = 1
        mock_popen.return_value = proc
        tg = next(s for s in SERVICES if s.key == "telegram")
        ok, msg = start_service(tg)
        self.assertFalse(ok)
        self.assertIn("exited immediately", msg)
        self.assertIn("TELEGRAM_BOT_TOKEN", msg)

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
    def test_status_report_format(self, mock_find) -> None:
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
                # unified manager — no separate prod_control hint
                self.assertNotIn("not managed by market_events start-all", text)


if __name__ == "__main__":
    unittest.main()
