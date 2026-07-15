"""FIX-5 Telegram runtime audit + getMe reachability."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot.research.futures_agent.telegram_runtime_audit import probe_get_me
from bot.research.market_events.telegram_ops.config_report import (
    format_telegram_config_report,
    telegram_api_reachable,
)


class TestTelegramRuntimeAuditFix5(unittest.TestCase):
    def test_probe_get_me_no_token(self) -> None:
        with patch(
            "bot.research.futures_agent.telegram_runtime_audit.get_telegram_bot_token",
            return_value="",
        ):
            out = probe_get_me("")
        self.assertFalse(out["ok"])
        self.assertEqual(out["exception"], "no_token")

    @patch("bot.research.futures_agent.telegram_runtime_audit.requests.get")
    def test_probe_get_me_http_ok(self, mock_get) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.ok = True
        resp.json.return_value = {"ok": True, "result": {"id": 42, "username": "auditbot"}}
        mock_get.return_value = resp
        out = probe_get_me("123456:ABC-DEF")
        self.assertTrue(out["ok"])
        self.assertEqual(out["http_code"], 200)
        self.assertEqual(out["username"], "auditbot")
        self.assertIsNone(out["exception"])
        url = mock_get.call_args.args[0]
        self.assertIn("/getMe", url)

    @patch("bot.research.market_events.telegram_ops.config_report.probe_telegram_get_me")
    def test_telegram_api_reachable_uses_getme(self, mock_probe) -> None:
        mock_probe.return_value = {"ok": True}
        self.assertTrue(telegram_api_reachable())
        mock_probe.return_value = {"ok": False}
        self.assertFalse(telegram_api_reachable())

    @patch("bot.research.market_events.telegram_ops.config_report.probe_telegram_get_me")
    def test_config_report_includes_getme_block(self, mock_probe) -> None:
        mock_probe.return_value = {
            "ok": False,
            "http_code": None,
            "response": None,
            "exception": "no_token",
            "username": None,
            "bot_id": None,
        }
        with patch(
            "bot.research.futures_agent.telegram_config.get_telegram_bot_token",
            return_value="",
        ):
            text = format_telegram_config_report()
        self.assertIn("(checked via getMe)", text)
        self.assertIn("reachable no", text)
        self.assertIn("getMe:", text)


if __name__ == "__main__":
    unittest.main()
