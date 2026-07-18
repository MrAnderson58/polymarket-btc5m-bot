"""V6.0.0 foundation tests — updated for 6.0.1 live render titles."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bot.terminal import __version__
from bot.terminal.models.dto import AccountCard, HomeCard, PortfolioCard, PositionCard, SignalCard
from bot.terminal.telegram.keyboards import main_menu_keyboard
from bot.terminal.telegram.render import (
    render_account,
    render_home,
    render_markets,
    render_portfolio,
    render_positions,
    render_settings,
    render_signals,
)
from bot.terminal.telegram.terminal_router import (
    SUPPORTED_TERMINAL_COMMANDS,
    dispatch_terminal_command,
)
from bot.terminal.models.dto import MarketsCard, MarketGroupCard


class TestTerminalFoundationV600(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_dtos(self) -> None:
        self.assertEqual(SignalCard(symbol="BTC", direction="LONG").symbol, "BTC")
        self.assertEqual(PositionCard(symbol="BTC", side="YES").side, "YES")
        self.assertIsInstance(PortfolioCard(), PortfolioCard)
        self.assertIsInstance(AccountCard(), AccountCard)

    def test_keyboard_main_menu(self) -> None:
        self.assertIn("inline_keyboard", main_menu_keyboard())

    def test_render_stubs(self) -> None:
        home = render_home(HomeCard(system_online=True, mode="paper"))
        self.assertIn("AI Trading Terminal", home)
        self.assertIn("AI Trading Terminal", render_markets(MarketsCard()))
        self.assertIn("AI Trading Terminal", render_signals([]))
        self.assertIn("AI Trading Terminal", render_positions([]))
        self.assertIn("AI Trading Terminal", render_portfolio(PortfolioCard()))
        self.assertIn("AI Trading Terminal", render_account(AccountCard()))
        self.assertIn("AI Trading Terminal", render_settings())

    def test_router_stubs_not_none(self) -> None:
        home = HomeCard(system_online=False, mode="paper")
        with patch(
            "bot.terminal.services.system_service.get_system_service",
        ) as gs, patch(
            "bot.terminal.services.market_service.get_market_service",
        ) as gm, patch(
            "bot.terminal.services.signals_service.get_signal_service",
        ) as gsig, patch(
            "bot.terminal.services.positions_service.get_position_service",
        ) as gp, patch(
            "bot.terminal.services.portfolio_service.get_portfolio_service",
        ) as gpf, patch(
            "bot.terminal.services.account_service.get_account_service",
        ) as ga:
            gs.return_value.get_home.return_value = home
            gm.return_value.get_markets.return_value = MarketsCard(
                groups=(MarketGroupCard(name="Crypto", symbols=("BTC",)),)
            )
            gsig.return_value.get_top.return_value = []
            gp.return_value.get_open.return_value = []
            gpf.return_value.get_summary.return_value = PortfolioCard()
            ga.return_value.get_balance.return_value = AccountCard()
            for cmd in sorted(SUPPORTED_TERMINAL_COMMANDS):
                reply = dispatch_terminal_command(cmd)
                self.assertIsNotNone(reply)
                assert reply is not None
                self.assertTrue(reply.ok)
                if cmd in {"/open", "/close", "/risk", "/sl", "/tp"}:
                    self.assertIn("Execution API ready", reply.text)
                elif cmd == "/brief":
                    self.assertIn("Good Morning", reply.text)
                elif cmd in {"/research", "/review"}:
                    self.assertTrue(
                        "Research" in reply.text or "Usage" in reply.text or "Decision" in reply.text
                    )
                elif cmd in {"/decision", "/why", "/timeline"}:
                    self.assertTrue(len(reply.text) > 0)
                else:
                    self.assertIn("AI Trading Terminal", reply.text)

    def test_router_ignores_unknown(self) -> None:
        self.assertIsNone(dispatch_terminal_command("/status"))


if __name__ == "__main__":
    unittest.main()
