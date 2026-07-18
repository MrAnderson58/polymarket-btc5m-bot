"""V6.0.1 — AI Trading Terminal live screens (read-only over existing data)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from bot.terminal.models.dto import (
    HomeCard,
    MarketGroupCard,
    MarketsCard,
    PortfolioCard,
    PositionCard,
    SignalCard,
)
from bot.terminal.telegram.keyboards import main_menu_keyboard
from bot.terminal.telegram.render import (
    render_home,
    render_markets,
    render_portfolio,
    render_positions,
    render_signals,
)
from bot.terminal.telegram.terminal_router import (
    SUPPORTED_TERMINAL_COMMANDS,
    dispatch_terminal_command,
)


class TestTerminalLiveV601(unittest.TestCase):
    def test_keyboard(self) -> None:
        kb = main_menu_keyboard()
        flat = [b["text"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("📈 Markets", flat)

    def test_render_home_uses_dto(self) -> None:
        text = render_home(
            HomeCard(
                system_online=True,
                mode="paper",
                equity=100.0,
                balance=100.0,
                open_positions=2,
                today_pnl=1.5,
                best_signal="BTC SHORT",
                last_ai_decision="BTC SHORT",
                workers_line="3/7 online",
                dashboard_line="ONLINE",
            )
        )
        self.assertIn("AI Trading Terminal", text)
        self.assertIn("🟢 ONLINE", text)
        self.assertIn("$100.00", text)
        self.assertIn("Today's PnL", text)

    def test_render_portfolio(self) -> None:
        text = render_portfolio(
            PortfolioCard(
                equity=105.0,
                cash=5.0,
                used_margin=100.0,
                open_risk=100.0,
                today_pnl=-2.0,
                week_pnl=3.0,
                winrate_pct=55.0,
                trades=10,
            )
        )
        self.assertIn("Paper Equity", text)
        self.assertIn("Winrate", text)

    def test_render_positions(self) -> None:
        text = render_positions([
            PositionCard(symbol="BTC", side="SHORT", entry=65000.0, size=100.0, risk=500.0, unrealized_pnl=-1.2),
        ])
        self.assertIn("BTC", text)
        self.assertIn("SHORT", text)
        self.assertIn("Entry", text)

    def test_render_signals_empty(self) -> None:
        self.assertIn("No active signals", render_signals([]))

    def test_render_markets(self) -> None:
        text = render_markets(
            MarketsCard(groups=(MarketGroupCard(name="Crypto", symbols=("BTC", "ETH")),))
        )
        self.assertIn("Crypto", text)
        self.assertIn("BTC", text)

    def test_router_home_mocked_services(self) -> None:
        home = HomeCard(
            system_online=True,
            mode="paper",
            equity=100.0,
            balance=100.0,
            open_positions=0,
            today_pnl=0.0,
            best_signal="—",
            last_ai_decision="—",
            workers_line="0/0",
            dashboard_line="—",
        )
        with patch(
            "bot.terminal.services.system_service.get_system_service",
        ) as gs:
            gs.return_value.get_home.return_value = home
            reply = dispatch_terminal_command("/home")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("AI Trading Terminal", reply.text)
        self.assertIn("Equity", reply.text)

    def test_router_commands_registered(self) -> None:
        for cmd in ("/home", "/markets", "/signals", "/positions", "/portfolio", "/account", "/settings"):
            self.assertIn(cmd, SUPPORTED_TERMINAL_COMMANDS)


if __name__ == "__main__":
    unittest.main()
