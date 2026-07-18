"""V7.1.0–V7.1.4 — E2E flow, session, timeline, explain, telemetry."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.terminal import __version__
from bot.terminal.commands import NavigateCommand, get_command_dispatcher, reset_command_dispatcher
from bot.terminal.commands.screen_controller import reset_screen_controller
from bot.terminal.explain import explain_score
from bot.terminal.scanner import ScannerRegistry, ScannerService, StaticScannerProvider
from bot.terminal.scanner.models import RankComponents
from bot.terminal.session import get_session_manager, reset_session_manager
from bot.terminal.telegram.keyboards import decision_keyboard, parse_callback_intent
from bot.terminal.telegram.terminal_router import (
    dispatch_terminal_command,
    handle_terminal_callback,
    is_terminal_command,
)
from bot.terminal.telemetry import get_telemetry, reset_telemetry
from bot.terminal.timeline import get_timeline_service, reset_timeline_service
from bot.terminal.watchlist import WatchlistService, WatchlistStore, reset_watchlist_service


class TestE2EFlowV710(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_manager()
        reset_telemetry()
        reset_timeline_service()
        reset_command_dispatcher()
        reset_screen_controller()
        reset_watchlist_service()
        self._tmp = tempfile.TemporaryDirectory()
        self.watch = WatchlistService(WatchlistStore(Path(self._tmp.name) / "w.json"))
        self.reg = ScannerRegistry()
        self.reg.register(StaticScannerProvider())
        self.scanner = ScannerService(self.reg)

    def tearDown(self) -> None:
        reset_session_manager()
        reset_telemetry()
        reset_timeline_service()
        reset_command_dispatcher()
        reset_screen_controller()
        reset_watchlist_service()
        self._tmp.cleanup()

    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_router_has_no_service_imports(self) -> None:
        path = Path(__file__).resolve().parents[1] / "bot/terminal/telegram/terminal_router.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        banned = {
            "get_signal_service",
            "get_portfolio_service",
            "get_watchlist_service",
            "get_decision_service",
            "get_alert_service",
            "get_morning_brief_service",
            "get_research_service",
            "get_system_service",
            "get_market_service",
            "get_position_service",
            "get_account_service",
            "get_portfolio_intelligence_service",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.assertNotIn(alias.name, banned)

    def _patches(self):
        return (
            patch("bot.terminal.scanner.get_scanner_service", return_value=self.scanner),
            patch("bot.terminal.scanner.scanner.get_scanner_service", return_value=self.scanner),
            patch("bot.terminal.decision.service.get_scanner_service", return_value=self.scanner),
            patch("bot.terminal.explain.why_symbol", side_effect=lambda sym: explain_score(
                RankComponents(trend=90, volume=80, learning=70, pattern=80, news=50, confidence=90, ai=85),
                symbol=sym,
            )),
            patch("bot.terminal.watchlist.get_watchlist_service", return_value=self.watch),
            patch("bot.terminal.watchlist.service.get_watchlist_service", return_value=self.watch),
        )

    def test_e2e_callback_chain_all_edit(self) -> None:
        chat = "e2e-1"
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            steps = [
                "term:home",
                "term:signals",
                "term:decision:BTC",
                "term:watchadd:BTC",
                "term:portfolio",
                "term:brief",
                "term:research:BTC",
            ]
            for cb in steps:
                reply = handle_terminal_callback(cb, user_id=chat)
                self.assertIsNotNone(reply)
                assert reply is not None
                self.assertTrue(reply.edit, msg=f"{cb} must edit")
                self.assertTrue(reply.ok)
                self.assertIsNotNone(reply.reply_markup)
                rows = reply.reply_markup.get("inline_keyboard") or []
                self.assertTrue(rows)

            session = get_session_manager().get_session(chat)
            self.assertEqual(session.symbol, "BTC")
            self.assertIn("BTC", self.watch.list_symbols(chat))

            events = get_timeline_service().for_symbol("BTC")
            kinds = {e.kind for e in events}
            self.assertTrue(kinds)
        finally:
            for p in patches:
                p.stop()

    def test_100_transitions_no_leak(self) -> None:
        chat = "stress"
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            screens = [
                "term:home",
                "term:signals",
                "term:portfolio",
                "term:brief",
                "term:watch",
                "term:markets",
            ]
            for i in range(100):
                cb = screens[i % len(screens)]
                reply = handle_terminal_callback(cb, user_id=chat)
                self.assertIsNotNone(reply)
                assert reply is not None
                self.assertTrue(reply.edit)

            self.assertEqual(get_session_manager().count(), 1)
            snap = get_telemetry().snapshot()
            self.assertGreaterEqual(sum(snap.screen_views.values()), 100)
        finally:
            for p in patches:
                p.stop()

    def test_decision_keyboard_has_flow(self) -> None:
        kb = decision_keyboard("BTC")
        flat = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("term:watchadd:BTC", flat)
        self.assertIn("term:portfolio", flat)
        self.assertIn("term:brief", flat)
        self.assertIn("term:research:BTC", flat)

    def test_parse_intents(self) -> None:
        self.assertEqual(parse_callback_intent("term:decision:BTC"), ("decision", "BTC"))
        self.assertEqual(parse_callback_intent("term:watchadd:ETH"), ("watch_add", "ETH"))
        self.assertEqual(parse_callback_intent("term:why:BTC"), ("why", "BTC"))


class TestSessionTimelineExplainTelemetry(unittest.TestCase):
    def setUp(self) -> None:
        reset_session_manager()
        reset_timeline_service()
        reset_telemetry()
        reset_command_dispatcher()
        reset_screen_controller()

    def tearDown(self) -> None:
        reset_session_manager()
        reset_timeline_service()
        reset_telemetry()
        reset_command_dispatcher()
        reset_screen_controller()

    def test_session_api(self) -> None:
        sm = get_session_manager()
        s = sm.get_session(42)
        self.assertEqual(s.chat_id, "42")
        s2 = sm.update_session(
            42, screen="signals", symbol="BTC", language="en", timezone="Europe/Moscow"
        )
        self.assertEqual(s2.screen, "signals")
        self.assertEqual(s2.symbol, "BTC")
        self.assertEqual(s2.timezone, "Europe/Moscow")
        sm.clear_session(42)
        self.assertEqual(sm.get_session(42).screen, "home")

    def test_timeline_readonly(self) -> None:
        tl = get_timeline_service()
        tl.record("BTC", "signal", "Signal generated", ts="09:30")
        tl.record("BTC", "decision", "Decision updated", ts="09:41")
        tl.record("BTC", "research", "Research reviewed", ts="10:15")
        tl.record("BTC", "portfolio", "Portfolio advice changed", ts="11:20")
        rows = tl.for_symbol("BTC")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0].ts, "09:30")
        self.assertEqual(rows[-1].message, "Portfolio advice changed")

    def test_why_explanation(self) -> None:
        expl = explain_score(
            RankComponents(
                trend=92, learning=70, pattern=90, volume=80, news=55, confidence=90, ai=88
            ),
            symbol="BTC",
        )
        self.assertGreater(expl.score, 0)
        text = expl.format_text()
        self.assertIn("Почему", text)
        self.assertIn("Trend", text)

    def test_why_command(self) -> None:
        self.assertTrue(is_terminal_command("/why BTC"))
        with patch(
            "bot.terminal.explain.why_symbol",
            return_value=explain_score(
                RankComponents(
                    trend=90, volume=80, learning=70, pattern=80, news=50, confidence=90, ai=85
                ),
                symbol="BTC",
            ),
        ):
            reply = dispatch_terminal_command("/why BTC", user_id="1")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("Score", reply.text)
        self.assertIn("Почему", reply.text)

    def test_dispatcher_ui_path(self) -> None:
        d = get_command_dispatcher()
        result = d.dispatch_ui(NavigateCommand(screen="home", chat_id="x", edit=True))
        self.assertTrue(result.ok)
        self.assertTrue(result.edit)
        self.assertIn("AI Trading Terminal", result.text)


if __name__ == "__main__":
    unittest.main()
