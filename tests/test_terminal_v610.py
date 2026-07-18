"""V6.1.0 — Interactive Telegram Terminal (navigation only)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bot.terminal import __version__
from bot.terminal.telegram.components import (
    CardRenderer,
    ProgressBarRenderer,
    SectionRenderer,
    StatusRenderer,
)
from bot.terminal.telegram.formatters import format_money, format_pct, format_duration, progress_bar, status_badge
from bot.terminal.telegram.keyboards import (
    home_keyboard,
    is_terminal_callback,
    parse_terminal_callback,
    screen_keyboard,
)
from bot.terminal.telegram.render import render_start
from bot.terminal.telegram.terminal_router import (
    dispatch_terminal_command,
    handle_terminal_callback,
    is_terminal_command,
)
from bot.terminal.models.dto import HomeCard


class TestFormattersV610(unittest.TestCase):
    def test_money_and_pct(self) -> None:
        self.assertEqual(format_money(102340), "$102,340.00")
        self.assertEqual(format_pct(3.24), "+3.24%")
        self.assertEqual(format_pct(-1.02), "-1.02%")

    def test_duration(self) -> None:
        self.assertEqual(format_duration(72 * 60 + 12), "1h 12m")
        self.assertEqual(format_duration(2 * 86400 + 4 * 3600), "2d 4h")

    def test_progress_bar(self) -> None:
        self.assertEqual(progress_bar(80), "████████░░ 80%")
        self.assertEqual(progress_bar(60), "██████░░░░ 60%")
        self.assertEqual(progress_bar(20), "██░░░░░░░░ 20%")

    def test_status_badges(self) -> None:
        self.assertEqual(status_badge("online"), "🟢 ONLINE")
        self.assertEqual(status_badge("warning"), "🟡 WARNING")
        self.assertEqual(status_badge("offline"), "🔴 OFFLINE")
        self.assertEqual(status_badge("???"), "⚪ UNKNOWN")


class TestComponentsV610(unittest.TestCase):
    def test_renderers(self) -> None:
        self.assertIn("ONLINE", StatusRenderer.render("online"))
        self.assertIn("80%", ProgressBarRenderer.render(80, label="Confidence"))
        self.assertIn("Title", SectionRenderer.render("Title", "a"))
        self.assertIn("Card", CardRenderer.render("Card", ["Body"]))


class TestNavigationV610(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_start_screen(self) -> None:
        text = render_start(HomeCard(system_online=True, mode="paper"))
        self.assertIn("AI Trading Terminal", text)
        self.assertIn("System Online", text)
        self.assertIn("Выберите раздел", text)

    def test_keyboards(self) -> None:
        home = home_keyboard()
        flat = [b["text"] for row in home["inline_keyboard"] for b in row]
        for label in (
            "📈 Markets",
            "🎯 Signals",
            "💼 Portfolio",
            "📊 Positions",
            "💰 Account",
            "⭐ Watch",
            "🔔 Alerts",
            "☀️ Brief",
            "⚙ Settings",
        ):
            self.assertIn(label, flat)
        screen = screen_keyboard()
        flat2 = [b["text"] for row in screen["inline_keyboard"] for b in row]
        self.assertIn("⬅ Back", flat2)
        self.assertIn("🏠 Home", flat2)

    def test_callback_parse(self) -> None:
        self.assertTrue(is_terminal_callback("term:signals"))
        self.assertEqual(parse_terminal_callback("term:portfolio"), "portfolio")

    def test_is_terminal_command(self) -> None:
        self.assertTrue(is_terminal_command("/start"))
        self.assertTrue(is_terminal_command("/home"))
        self.assertFalse(is_terminal_command("/status"))

    def test_dispatch_start(self) -> None:
        with patch(
            "bot.terminal.services.system_service.get_system_service",
        ) as gs:
            gs.return_value.get_home.return_value = HomeCard(system_online=True, mode="paper")
            reply = dispatch_terminal_command("/start")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertFalse(reply.edit)
        self.assertIn("AI Trading Terminal", reply.text)
        self.assertIsNotNone(reply.reply_markup)

    def test_callback_edits(self) -> None:
        with patch(
            "bot.terminal.services.signals_service.get_signal_service",
        ) as gs:
            gs.return_value.get_top.return_value = []
            reply = handle_terminal_callback("term:signals")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertTrue(reply.edit)
        self.assertIn("Signals", reply.text)
        flat = [b["text"] for row in reply.reply_markup["inline_keyboard"] for b in row]
        self.assertIn("⬅ Back", flat)


if __name__ == "__main__":
    unittest.main()
