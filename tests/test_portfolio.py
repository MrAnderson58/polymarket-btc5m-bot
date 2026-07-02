"""Portfolio layer tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.portfolio.guards import check_portfolio_guards
from bot.portfolio.kill_switch import is_kill_switch_active
from bot.portfolio.portfolio import PortfolioManager
from bot.portfolio.sizing import effective_position_size_usdc, max_allowed_usdc
from bot.risk import check_can_open_position


class PortfolioLayerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_micro_sizing_cap(self) -> None:
        with mock.patch("bot.portfolio.sizing.LIVE_MODE", "micro"):
            self.assertEqual(max_allowed_usdc(), 1.0)
            self.assertEqual(effective_position_size_usdc(), 1.0)

    def test_kill_switch_file(self) -> None:
        flag = Path(self._tmpdir.name) / "LIVE_DISABLED"
        with mock.patch("bot.portfolio.kill_switch.KILL_SWITCH_FILE", flag):
            with mock.patch("bot.portfolio.kill_switch.LIVE_ENABLED", True):
                self.assertFalse(is_kill_switch_active())
                flag.write_text("paused", encoding="utf-8")
                self.assertTrue(is_kill_switch_active())

    def test_daily_loss_guard_pauses(self) -> None:
        with connect(self.db_path) as conn:
            manager = PortfolioManager.load(conn)
            manager.state.balance = 100.0
            manager.save(conn)
            conn.execute(
                """
                INSERT INTO early_reversion_v2_trades (
                    market_slug, window_start_ts, end_ts, side, strategy_name,
                    entry_price, entry_ts, status, exit_price, exit_reason,
                    pnl_percent, pnl_usdc, holding_time_seconds, closed_at
                ) VALUES (
                    'btc-updown-5m-test', 1, 300, 'NO', 'NO_C',
                    0.38, 100, 'closed', 0.35, 'STOP_LOSS',
                    -10, -6.0, 30, datetime('now')
                )
                """
            )
            conn.commit()
            guard = check_portfolio_guards(conn)
            self.assertFalse(guard.allowed)
            self.assertIn("daily loss", (guard.reason or "").lower())

    def test_risk_check_respects_kill_switch_in_live_mode(self) -> None:
        flag = Path(self._tmpdir.name) / "LIVE_DISABLED"
        flag.write_text("x", encoding="utf-8")
        with mock.patch("bot.portfolio.kill_switch.KILL_SWITCH_FILE", flag):
            with mock.patch("bot.risk.is_live_trading_enabled", return_value=True):
                with connect(self.db_path) as conn:
                    result = check_can_open_position(conn)
        self.assertFalse(result.allowed)
        self.assertIn("KILL SWITCH", result.reason or "")


if __name__ == "__main__":
    unittest.main()
