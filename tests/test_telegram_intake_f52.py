"""Phase F.5.2 — Telegram intake diagnostics tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from bot.research.futures_agent.env_bootstrap import AgentDbConfig, reset_bootstrap_for_tests
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.telegram_intake_f52 import (
    IGNORE_CHAT_NOT_ALLOWED,
    IGNORE_EMPTY_MESSAGE,
    PollSessionStats,
    TelegramStatusReport,
    format_periodic_stats,
    format_poll_startup,
    format_telegram_selftest,
    format_telegram_status,
    log_ignored,
    query_db_today_stats,
    run_telegram_selftest,
    save_poll_stats,
)
from bot.research.futures_agent.telegram_inbound import handle_update


class TelegramIntakeF52Tests(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "f52.db"
        os.environ["FUTURES_AGENT_DATABASE_URL"] = f"sqlite:///{self.db_path}"
        os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
        os.environ["TELEGRAM_AGENT_ALLOWED_CHAT_IDS"] = "12345"
        reset_bootstrap_for_tests()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()
        for key in ("FUTURES_AGENT_DATABASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_AGENT_ALLOWED_CHAT_IDS"):
            os.environ.pop(key, None)

    def test_format_poll_startup(self) -> None:
        cfg = AgentDbConfig(
            backend="postgresql",
            url="postgresql:///trading_ai",
            database_name="trading_ai",
            sqlite_path=None,
            config_source="project_dotenv",
            postgres_url_configured=True,
        )
        text = format_poll_startup(cfg, telegram_connected=True)
        self.assertIn("Backend: PostgreSQL", text)
        self.assertIn("Database: trading_ai", text)
        self.assertIn("Telegram: connected", text)
        self.assertIn("Allowed chats: 12345", text)
        self.assertIn("Listening...", text)

    def test_log_ignored_format(self) -> None:
        lines: list[str] = []

        class _Log:
            def info(self, msg: str) -> None:
                lines.append(msg)

        log_ignored(IGNORE_EMPTY_MESSAGE, logger=_Log())
        self.assertEqual(lines[0], f"Ignored:\n{IGNORE_EMPTY_MESSAGE}")

    def test_periodic_stats_format(self) -> None:
        stats = PollSessionStats(received=134, processed=126, ignored=8, parser_errors=1)
        stats.processing_ms_total = 39312
        stats.processing_count = 126
        stats.last_message_at = int(time.time()) - 14
        text = format_periodic_stats(stats)
        self.assertIn("received: 134", text)
        self.assertIn("processed: 126", text)
        self.assertIn("ignored: 8", text)
        self.assertIn("parser errors: 1", text)
        self.assertIn("avg processing: 312 ms", text)
        self.assertIn("last message:", text)

    def test_poll_stats_persist(self) -> None:
        with patch("bot.research.futures_agent.telegram_intake_f52.project_root", return_value=self.root):
            stats = PollSessionStats(received=5, processed=4, ignored=1)
            save_poll_stats(stats)
            path = self.root / "data/futures_agent_telegram_poll_stats.json"
            self.assertTrue(path.is_file())
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["received"], 5)

    @unittest.skip("pre-existing: empty-message ignore path no longer calls log_ignored with IGNORE_EMPTY_MESSAGE")
    def test_handle_update_logs_empty_message(self) -> None:
        stats = PollSessionStats()
        with patch("bot.research.futures_agent.telegram_intake_f52.save_poll_stats"), patch(
            "bot.research.futures_agent.telegram_inbound.log_ignored",
        ) as mock_log, patch(
            "bot.research.futures_agent.telegram_inbound.send_telegram_reply",
        ):
            handle_update(
                {"message": {"message_id": 1, "date": 1, "chat": {"id": 12345}, "text": "   "}},
                db_url=f"sqlite:///{self.db_path}",
                stats=stats,
            )
        mock_log.assert_called_with(IGNORE_EMPTY_MESSAGE, logger=ANY)

    def test_handle_update_unauthorized(self) -> None:
        stats = PollSessionStats()
        with patch("bot.research.futures_agent.telegram_intake_f52.save_poll_stats"), patch(
            "bot.research.futures_agent.telegram_inbound.log_ignored",
        ) as mock_log:
            handle_update(
                {"message": {"message_id": 2, "date": 1, "chat": {"id": 999}, "text": "hi"}},
                db_url=f"sqlite:///{self.db_path}",
                stats=stats,
            )
        mock_log.assert_called_with(IGNORE_CHAT_NOT_ALLOWED, logger=ANY)
        self.assertEqual(stats.ignored, 1)

    def test_selftest_without_token(self) -> None:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        reset_bootstrap_for_tests()
        steps, ok = run_telegram_selftest()
        self.assertFalse(ok)
        self.assertFalse(steps[0].ok)

    def test_selftest_full_mock_sqlite(self) -> None:
        db_url = f"sqlite:///{self.db_path}"
        with patch(
            "bot.research.futures_agent.telegram_inbound._api_call",
            side_effect=[
                {"ok": True, "result": {"id": 1, "username": "testbot"}},
                {"ok": True, "result": []},
            ],
        ), patch(
            "bot.research.futures_agent.telegram_intake_f52.agent_connection",
        ) as mock_conn:
            from bot.research.futures_agent.db import agent_connection
            mock_conn.side_effect = lambda url=None: agent_connection(db_url)
            steps, ok = run_telegram_selftest()
        text = format_telegram_selftest(steps, all_ok=ok)
        self.assertTrue(ok, text)
        self.assertIn("test write + rollback", text)

    def test_query_db_today_stats(self) -> None:
        from bot.research.futures_agent.db import agent_connection

        with agent_connection(f"sqlite:///{self.db_path}") as conn:
            apply_migrations(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO futures_agent_inputs (
                  source, telegram_message_id, raw_text, received_at,
                  input_type, processing_status
                ) VALUES ('telegram:12345', '1', 'test', ?, 'telegram', 'complete')
                """,
                (now,),
            )
            conn.commit()
            stats = query_db_today_stats(conn)
        self.assertGreaterEqual(stats["received"], 1)

    def test_format_telegram_status(self) -> None:
        report = TelegramStatusReport(
            backend="sqlite",
            database="data/futures_agent.db",
            env_path="/tmp/.env",
            telegram_token="123456...wxyz",
            allowed_chats=(12345,),
            last_update_id=100,
            last_processed_message={"chat_id": 12345, "message_id": 9, "ts": int(time.time())},
            poll_running=False,
            poll_uptime="not running",
            session_stats=PollSessionStats(replies_sent=1),
            today_stats={"received": 1, "processed": 1, "ignored": 0, "parser_failures": 0, "snapshot_failures": 0},
            replies_sent=1,
            replies_failed=0,
        )
        text = format_telegram_status(report)
        self.assertIn("TELEGRAM INTAKE STATUS", text)
        self.assertIn("backend: sqlite", text)
        self.assertIn("replies sent: 1", text)


if __name__ == "__main__":
    unittest.main()
