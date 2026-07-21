"""S49 — Trader UI & Signal-Centric Telegram tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.telegram_terminal import (
    TRADER_COMMANDS,
    build_report_completion_message,
    build_reports_keyboard,
    build_trader_report_message,
    handle_ai_callback,
    handle_ai_research_command_sync,
    parse_ai_command_args,
    requires_interactive_handler,
    run_interactive_report,
)
from bot.research.ai_analyst.trader_report import (
    format_news_headline,
    format_top_news_compact,
    format_trader_report_html,
    news_polarity_icon,
    shorten_news_title,
)


class TestNewsFormatterS49(unittest.TestCase):
    def test_icons_by_polarity_and_score(self) -> None:
        self.assertEqual(news_polarity_icon({"polarity": "positive"}), "🟢")
        self.assertEqual(news_polarity_icon({"polarity": "negative"}), "🔴")
        self.assertEqual(news_polarity_icon({"net_score": 0.8}), "🟢")
        self.assertEqual(news_polarity_icon({"net_score": -0.5}), "🔴")
        self.assertEqual(news_polarity_icon({"net_score": 0.0}), "🟡")

    def test_headline_is_icon_plus_title_only(self) -> None:
        line = format_news_headline({
            "title": "BlackRock ETF +420M",
            "polarity": "positive",
            "why_it_matters": "This long explanation must not appear in trader UI.",
            "summary": "Also too long for the terminal.",
        })
        self.assertEqual(line, "🟢 BlackRock ETF +420M")
        self.assertNotIn("long explanation", line)
        self.assertNotIn("Also too long", line)

    def test_title_shortened(self) -> None:
        long = "A" * 120
        self.assertTrue(len(shorten_news_title(long)) <= 72)

    def test_top_news_compact_block(self) -> None:
        html = format_top_news_compact([
            {"title": "ETF +420M", "polarity": "positive"},
            {"title": "Whale moved 18k BTC", "polarity": "negative"},
            {"title": "CPI today", "polarity": "neutral"},
        ])
        self.assertIn("🟢 ETF +420M", html)
        self.assertIn("🔴 Whale moved 18k BTC", html)
        self.assertIn("🟡 CPI today", html)
        self.assertNotIn("Why it matters", html)


class TestTraderReportFormatterS49(unittest.TestCase):
    def test_signal_card_shape(self) -> None:
        sig = TradingSignal(
            symbol="BTC",
            direction="LONG",
            entry_low=64000,
            entry_high=64500,
            stop_loss=63000,
            tp1=65000,
            tp2=66000,
            tp3=68000,
            risk_pct=1.0,
            confidence=72,
            reasons=["ETF inflows", "Neutral funding", "Bullish structure"],
        )
        ctx = {
            "analysis_quality": {"confidence": 68},
            "macro": {"dxy": {"change_pct": -0.2}, "us10y": {"yield": 4.2}},
            "etf": {"btc_etf": {"netflow_1d": 420, "netflow_5d": 900, "trend": "Bullish"}},
            "intelligence": {
                "top_events": [
                    {"title": "ETF +420M", "polarity": "positive"},
                    {"title": "Binance Listing", "polarity": "positive"},
                ],
            },
        }
        html = format_trader_report_html(ctx, signal=sig)
        self.assertIn("BTC LONG", html)
        self.assertIn("Confidence", html)
        self.assertIn("Entry", html)
        self.assertIn("Stop", html)
        self.assertIn("TP1", html)
        self.assertIn("TP2", html)
        self.assertIn("TP3", html)
        self.assertIn("Risk", html)
        self.assertIn("Market Score", html)
        self.assertIn("Macro", html)
        self.assertIn("Flow", html)
        self.assertIn("TOP NEWS", html)
        self.assertIn("AI VERDICT", html)
        self.assertIn("ETF inflows", html)
        self.assertNotIn("Why it matters", html)
        self.assertLessEqual(html.count("\n"), 45)

    def test_wait_when_no_signal(self) -> None:
        html = format_trader_report_html({"intelligence": {"top_events": []}}, signal=None)
        self.assertIn("WAIT", html)


class TestTelegramMenuS49(unittest.TestCase):
    def test_trader_commands_only_in_primary_set(self) -> None:
        self.assertEqual(
            TRADER_COMMANDS,
            frozenset({"/report", "/signals", "/open", "/stats", "/doctor"}),
        )

    def test_keyboard_labels(self) -> None:
        kb = build_reports_keyboard()
        labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(
            labels,
            ["📈 Report", "📊 Signals", "📂 Open", "📉 Stats", "⚙ Doctor"],
        )


class TestDebugModeS49(unittest.TestCase):
    def test_debug_report_is_interactive(self) -> None:
        self.assertTrue(requires_interactive_handler("/debug", ["report"]))
        self.assertTrue(requires_interactive_handler("/debug", []))
        self.assertFalse(requires_interactive_handler("/doctor", []))

    def test_parse_debug_args(self) -> None:
        cmd, args = parse_ai_command_args("/debug report")
        self.assertEqual(cmd, "/debug")
        self.assertEqual(args, ["report"])

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name)
        ctx = {
            "data_timestamps": {"market": "now", "macro": "now", "intelligence": "now", "context": "now"},
            "intelligence": {"top_events": [{"title": "ETF", "polarity": "positive"}]},
            "reasoning_hints": {"narrative_candidates": []},
            "analysis_quality": {"confidence": 50, "data_gaps": []},
            "context_completeness": 50,
        }
        (self.reports / "market_context.json").write_text(json.dumps(ctx), encoding="utf-8")
        (self.reports / "market_report.md").write_text(
            "## Executive Summary\n\nToday's Theme:\nRisk on\n\nMarket Bias:\nBullish\n",
            encoding="utf-8",
        )
        (self.reports / "telegram_post.md").write_text("Full editorial post", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_debug_message_includes_full_stack(self) -> None:
        text = build_report_completion_message(reports_dir=self.reports)
        self.assertIn("DEBUG REPORT", text)
        self.assertIn("TODAY", text.upper())
        self.assertIn("TELEGRAM", text.upper())
        self.assertIn("Full analytics retained", text)

    @patch("bot.research.ai_analyst.telegram_terminal._regenerate_full")
    @patch("bot.research.ai_analyst.telegram_terminal.build_report_completion_message")
    def test_interactive_debug_uses_full_report(
        self, mock_debug: MagicMock, mock_regen: MagicMock,
    ) -> None:
        mock_regen.return_value = {"ok": True}
        mock_debug.return_value = "<b>DEBUG FULL</b>"
        edits: list[str] = []

        def edit(body: str, _markup: dict | None) -> bool:
            edits.append(body)
            return True

        delivery = run_interactive_report(
            cmd="/debug",
            args=["report"],
            edit_message=edit,
            reports_dir=self.reports,
        )
        self.assertTrue(delivery.already_delivered)
        self.assertIn("DEBUG FULL", edits[-1])
        mock_debug.assert_called_once()

    @patch("bot.research.ai_analyst.telegram_terminal._regenerate_full")
    @patch("bot.research.ai_analyst.telegram_terminal.build_trader_report_message")
    def test_default_report_not_debug(
        self, mock_trader: MagicMock, mock_regen: MagicMock,
    ) -> None:
        mock_regen.return_value = {"ok": True}
        mock_trader.return_value = "<b>BTC LONG</b>"
        edits: list[str] = []

        def edit(body: str, _markup: dict | None) -> bool:
            edits.append(body)
            return True

        run_interactive_report(cmd="/report", edit_message=edit, reports_dir=self.reports)
        mock_trader.assert_called_once()
        self.assertIn("BTC LONG", edits[-1])


class TestDoctorAndCallbacksS49(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name)
        (self.reports / "market_context.json").write_text(
            json.dumps({
                "btc": {"price": 65000, "trend": "bullish", "change_24h_pct": 1.2},
                "analysis_quality": {"confidence": 70},
                "intelligence": {"top_events": []},
                "etf": {"btc_etf": {"netflow_5d": 100}},
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @patch("bot.research.market_events.doctor.run_doctor", return_value="Everything OK")
    def test_doctor_command(self, _mock: MagicMock) -> None:
        delivery = handle_ai_research_command_sync("/doctor")
        self.assertIn("PLATFORM DOCTOR", delivery.text.upper())
        self.assertIn("Everything OK", delivery.text)
        self.assertIsNotNone(delivery.reply_markup)

    def test_report_callback_builds_trader_card(self) -> None:
        with patch(
            "bot.research.ai_analyst.telegram_terminal.load_latest_signal_row",
            return_value=None,
        ):
            delivery = handle_ai_callback("report", reports_dir=self.reports)
        self.assertTrue("BTC" in delivery.text or "WAIT" in delivery.text)
        self.assertIsNotNone(delivery.reply_markup)

    def test_build_trader_from_disk(self) -> None:
        with patch(
            "bot.research.ai_analyst.telegram_terminal.load_latest_signal_row",
            return_value=None,
        ):
            text = build_trader_report_message(reports_dir=self.reports)
        self.assertTrue("LONG" in text or "WAIT" in text)


if __name__ == "__main__":
    unittest.main()
