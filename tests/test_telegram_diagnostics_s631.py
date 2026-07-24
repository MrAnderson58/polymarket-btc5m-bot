"""S63.1 Telegram diagnostics tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.signal_intelligence import telegram_diagnostics_s631 as s631


class TestTelegramDiagnosticsS631(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "system"
        self.now = int(time.time())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_report_exports_with_mocked_probes(self) -> None:
        with (
            patch.object(s631, "_check_collector", return_value={
                "ok": False,
                "collector_running": False,
                "lock_held": False,
                "process_found": False,
                "probable_reason": "not running",
                "recommended_fix": "start telegram-poll",
            }),
            patch.object(s631, "_check_authorization", return_value={
                "ok": True,
                "token_configured": True,
                "telegram_connected": True,
                "allowed_chat_ids": [1],
                "allowed_chats_count": 1,
                "missing_permissions": [],
                "diagnose": {},
            }),
            patch.object(s631, "_check_dialogs_channels", return_value={
                "ok": True,
                "dialogs_api": "not_available",
                "dialogs_available": 1,
                "allowed_chat_ids": [1],
                "channels_available_s44": [],
            }),
            patch.object(s631, "_check_messages", return_value={
                "ok": False,
                "last_message_id": 42,
                "last_successful_collection_ts": self.now - 100000,
                "messages_last_hour": 0,
                "messages_last_24h": 0,
                "messages_per_hour": 0.0,
                "probable_reason": "stale",
                "recommended_fix": "check collector",
            }),
            patch.object(s631, "_check_summarizer", return_value={
                "ok": True,
                "last_summary_id": 7,
                "last_summary_ts": self.now,
                "summaries_last_24h": 1,
                "summarizer": "f2",
            }),
            patch.object(s631, "_check_database_inserts", return_value={
                "ok": True,
                "state": {"inbound_traces_24h": 0},
                "inbound_traces_24h": 0,
                "inbound_errors_24h": 0,
                "delivery_sent_24h": 0,
                "delivery_failed_24h": 0,
            }),
            patch.object(s631, "_check_daily_report", return_value={
                "ok": True,
                "morning_report_exists": False,
                "morning_report_path": "x",
            }),
            patch.object(s631, "_tail_log_errors", return_value={
                "ok": True,
                "errors": ["sample error line"],
                "exceptions": [],
                "log_path": "me-telegram.log",
            }),
        ):
            out = s631.run_telegram_diagnostics(
                now=self.now,
                report_dir=self.out,
                root=Path(self.tmp.name),
            )

        self.assertEqual(out.get("stage"), "S63.1")
        self.assertFalse(out.get("ok"))
        self.assertGreaterEqual(len(out.get("failures") or []), 1)
        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())
        text = md.read_text(encoding="utf-8")
        self.assertIn("Telegram Health (S63.1)", text)
        self.assertIn("Collector status", text)
        self.assertIn("Recommended fix", text)
        self.assertIn("Diagnostics only", text)

    def test_cli_registered(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("telegram-health-report", proc.stdout)


if __name__ == "__main__":
    unittest.main()
