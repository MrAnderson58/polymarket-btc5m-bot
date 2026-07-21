"""S46.4 — Telegram Research Terminal tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.ai_analyst.config import load_telegram_terminal_settings
from bot.research.ai_analyst.report_cache import (
    ensure_fresh_report,
    is_report_fresh,
    report_path,
)
from bot.research.ai_analyst.telegram_formatter import (
    extract_executive_summary,
    format_error_html,
    format_executive_summary_html,
    markdown_to_telegram_html,
)
from bot.research.ai_analyst.telegram_terminal import (
    AI_RESEARCH_COMMANDS,
    build_reports_keyboard,
    format_progress_message,
    handle_ai_callback,
    handle_ai_research_command_sync,
    is_ai_research_command,
    parse_ai_callback,
    run_interactive_report,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    SUPPORTED_COMMANDS,
)


class TestCommandRegistrationS464(unittest.TestCase):
    def test_ai_commands_registered(self) -> None:
        for cmd in ("/report", "/btc", "/macro", "/sp500", "/events", "/narrative", "/context"):
            self.assertIn(cmd, SUPPORTED_COMMANDS)
        self.assertIn("/market", SUPPORTED_COMMANDS)
        self.assertIn("/health", SUPPORTED_COMMANDS)

    def test_is_ai_research_command(self) -> None:
        self.assertTrue(is_ai_research_command("/report"))
        self.assertTrue(is_ai_research_command("/market@MyBot"))
        self.assertFalse(is_ai_research_command("/status"))
        self.assertEqual(len(AI_RESEARCH_COMMANDS), 9)


class TestReportCacheS464(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fresh_and_stale_ttl(self) -> None:
        path = report_path("market", reports_dir=self.reports)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# report\n", encoding="utf-8")
        self.assertTrue(is_report_fresh("market", ttl_minutes=15, reports_dir=self.reports))

        old = time.time() - 20 * 60
        import os
        os.utime(path, (old, old))
        self.assertFalse(is_report_fresh("market", ttl_minutes=15, reports_dir=self.reports))

    def test_regenerate_only_when_needed(self) -> None:
        calls: list[str] = []

        def regen() -> dict:
            calls.append("regen")
            report_path("market", reports_dir=self.reports).write_text("new", encoding="utf-8")
            return {"ok": True}

        path, regen_flag = ensure_fresh_report(
            "market",
            ttl_minutes=15,
            reports_dir=self.reports,
            regenerate=regen,
        )
        self.assertTrue(regen_flag)
        self.assertEqual(calls, ["regen"])

        path2, regen_flag2 = ensure_fresh_report(
            "market",
            ttl_minutes=15,
            reports_dir=self.reports,
            regenerate=regen,
        )
        self.assertFalse(regen_flag2)
        self.assertEqual(calls, ["regen"])


class TestFormattingS464(unittest.TestCase):
    def test_executive_summary_html(self) -> None:
        md = "\n".join([
            "## Executive Summary",
            "",
            "Today's Theme:",
            "Risk assets resilient.",
            "",
            "Market Bias:",
            "Moderately Bullish",
        ])
        block = extract_executive_summary(md)
        html = format_executive_summary_html(block)
        self.assertIn("TODAY'S THEME", html)
        self.assertIn("MARKET BIAS", html)
        self.assertNotIn("**", html)

    def test_error_html(self) -> None:
        err = format_error_html("timeout")
        self.assertIn("Report generation failed", err)
        self.assertIn("timeout", err)

    def test_markdown_to_html(self) -> None:
        out = markdown_to_telegram_html("## Title\n\n- bullet")
        self.assertIn("<b>", out)
        self.assertIn("•", out)


class TestKeyboardAndCallbackS464(unittest.TestCase):
    def test_keyboard_structure(self) -> None:
        kb = build_reports_keyboard()
        rows = kb["inline_keyboard"]
        self.assertEqual(len(rows), 2)
        data = {btn["callback_data"] for row in rows for btn in row}
        self.assertIn("ai:market", data)
        self.assertIn("ai:events", data)

    def test_parse_callback(self) -> None:
        self.assertEqual(parse_ai_callback("ai:btc"), "btc")
        self.assertIsNone(parse_ai_callback("term:home"))

    def test_progress_message(self) -> None:
        msg = format_progress_message({"Context": True, "Intelligence": False})
        self.assertIn("✓ Context", msg)
        self.assertIn("… Intelligence", msg)


class TestHandlersS464(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name)
        ctx = {
            "context_completeness": 72,
            "analysis_quality": {"confidence": 68, "data_gaps": ["polymarket"]},
            "intelligence": {
                "top_events": [{
                    "title": "ETF inflows",
                    "net_score": 0.8,
                    "why_it_matters": "Flows matter",
                    "symbols": ["BTC"],
                }],
            },
            "reasoning_hints": {
                "narrative_candidates": [{
                    "narrative": "ETF Demand",
                    "strength": "Strong",
                    "evidence": "netflow",
                }],
            },
        }
        (self.reports / "market_context.json").write_text(
            json.dumps(ctx), encoding="utf-8",
        )
        (self.reports / "btc_brief.md").write_text("## BTC Brief\n\n### Market State\nok", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @patch("bot.research.ai_analyst.telegram_terminal.run_ai_analyst")
    def test_btc_uses_cache_when_fresh(self, mock_run: MagicMock) -> None:
        old = time.time() - 60
        import os
        os.utime(self.reports / "btc_brief.md", (old, old))
        delivery = handle_ai_research_command_sync("/btc", reports_dir=self.reports)
        mock_run.assert_not_called()
        self.assertIn("BTC BRIEF", delivery.text)

    def test_events_from_context(self) -> None:
        delivery = handle_ai_research_command_sync("/events", reports_dir=self.reports)
        self.assertIn("ETF inflows", delivery.text)
        self.assertIn("Why it matters", delivery.text)

    def test_callback_no_regeneration(self) -> None:
        with patch("bot.research.ai_analyst.telegram_terminal.run_ai_analyst") as mock_run:
            delivery = handle_ai_callback("btc", reports_dir=self.reports)
            mock_run.assert_not_called()
            self.assertIn("MARKET STATE", delivery.text)

    @patch("bot.research.ai_analyst.telegram_terminal._regenerate_full")
    def test_interactive_report_error(self, mock_regen: MagicMock) -> None:
        mock_regen.side_effect = RuntimeError("LLM down")
        edits: list[str] = []

        def edit(body: str, _markup: dict | None) -> bool:
            edits.append(body)
            return True

        delivery = run_interactive_report(cmd="/report", edit_message=edit, reports_dir=self.reports)
        self.assertTrue(delivery.already_delivered)
        self.assertTrue(any("Report generation failed" in e for e in edits))

    @patch("bot.research.ai_analyst.telegram_terminal._regenerate_full")
    @patch("bot.research.ai_analyst.telegram_terminal.build_report_completion_message")
    def test_interactive_report_success(self, mock_msg: MagicMock, mock_regen: MagicMock) -> None:
        mock_regen.return_value = {"ok": True}
        mock_msg.return_value = "<b>done</b>"
        edits: list[str] = []

        markups: list[dict | None] = []

        def edit(body: str, markup: dict | None) -> bool:
            edits.append(body)
            markups.append(markup)
            return True

        delivery = run_interactive_report(cmd="/report", edit_message=edit, reports_dir=self.reports)
        self.assertTrue(delivery.already_delivered)
        self.assertIn("<b>done</b>", edits[-1])
        self.assertIsNotNone(markups[-1])


class TestTelegramSettingsS464(unittest.TestCase):
    def test_defaults(self) -> None:
        s = load_telegram_terminal_settings()
        self.assertEqual(s.report_cache_minutes, 15)
        self.assertTrue(s.enable_progress_messages)
        self.assertEqual(s.parse_mode, "HTML")


if __name__ == "__main__":
    unittest.main()
