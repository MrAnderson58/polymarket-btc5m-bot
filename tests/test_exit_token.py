"""Tests for exit token_id resolution and pre-submit tradability checks."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db, insert_early_reversion_v2_trade, insert_order_intent
from bot.execution import (
    ExitOrder,
    close_early_reversion_position,
    resolve_exit_token_id,
)


class ExitTokenResolutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRADING_MODE": "live",
                "LIVE_EXIT_ENABLED": "true",
                "MAX_OPEN_POSITIONS": "5",
                "MAX_DAILY_LOSS_USDC": "10",
            },
            clear=False,
        )
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.execution as execution

        importlib.reload(config)
        importlib.reload(execution)
        self.execution = execution

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _seed_entry_intent(self, conn: sqlite3.Connection, *, token_id: str = "entry-token") -> None:
        insert_order_intent(
            conn,
            idempotency_key="v2:NO_C:btc-updown-5m-old:NO:entry",
            trading_mode="live",
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-old",
            side="NO",
            token_id=token_id,
            price=0.40,
            size_usdc=1.0,
            shares=2.5,
            status="submitted",
        )
        conn.commit()

    def test_entry_token_preferred_over_active_market_hint(self) -> None:
        with connect(self.db_path) as conn:
            self._seed_entry_intent(conn, token_id="entry-token-no")
            token_id, shares = resolve_exit_token_id(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-old",
                side="NO",
                entry_price=0.40,
                size_usdc=1.0,
                hint_token_id="wrong-active-market-token",
            )
        self.assertEqual(token_id, "entry-token-no")
        self.assertAlmostEqual(shares, 2.5)

    @mock.patch(
        "bot.market_scanner.get_token_id_for_market_slug_and_side",
        return_value="gamma-token-no",
    )
    def test_gamma_fallback_when_no_entry_intent(self, _mock_gamma: mock.MagicMock) -> None:
        with connect(self.db_path) as conn:
            token_id, _ = resolve_exit_token_id(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-old",
                side="NO",
                entry_price=0.40,
                size_usdc=1.0,
                hint_token_id="active-hint",
            )
        self.assertEqual(token_id, "gamma-token-no")

    def _seed_open_trade(self, conn: sqlite3.Connection) -> sqlite3.Row:
        self._seed_entry_intent(conn, token_id="entry-token-no")
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug="btc-updown-5m-old",
            window_start_ts=1_700_000_000,
            end_ts=1_700_000_300,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.40,
            entry_ts=1_700_000_010,
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM early_reversion_v2_trades WHERE id = ?",
            (trade_id,),
        ).fetchone()

    @mock.patch("bot.market_scanner.is_exit_token_tradable", return_value=False)
    @mock.patch("bot.execution._submit_live_sell")
    @mock.patch("bot.exit_recovery.reconcile_failed_exit_for_order", return_value=False)
    def test_skip_sell_when_token_not_tradable(
        self,
        _mock_reconcile: mock.MagicMock,
        mock_sell: mock.MagicMock,
        _mock_tradable: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade = self._seed_open_trade(conn)
            closed = {"done": False}

            def _finalize() -> None:
                closed["done"] = True

            result = close_early_reversion_position(
                conn,
                strategy_version="v2",
                trade=trade,
                token_id="wrong-active-market-token",
                bid=0.35,
                exit_reason="STOP_LOSS",
                size_usdc=1.0,
                close_trade=_finalize,
            )
            conn.commit()

        self.assertFalse(result)
        self.assertFalse(closed["done"])
        mock_sell.assert_not_called()

        with connect(self.db_path) as conn:
            intent = conn.execute(
                """
                SELECT status, token_id, error_message
                FROM order_intents
                WHERE idempotency_key = 'v2:NO_C:btc-updown-5m-old:NO:exit'
                """
            ).fetchone()
        self.assertIsNotNone(intent)
        self.assertEqual(intent["status"], "failed")
        self.assertEqual(intent["token_id"], "entry-token-no")
        self.assertIn("no CLOB orderbook", intent["error_message"])

    @mock.patch("bot.market_scanner.is_exit_token_tradable", return_value=True)
    @mock.patch("bot.execution._submit_live_sell")
    def test_close_uses_entry_token_not_hint(
        self,
        mock_sell: mock.MagicMock,
        _mock_tradable: mock.MagicMock,
    ) -> None:
        mock_sell.return_value = (True, "sell-1", None)
        with connect(self.db_path) as conn:
            trade = self._seed_open_trade(conn)

            def _finalize() -> None:
                conn.execute(
                    "UPDATE early_reversion_v2_trades SET status = 'closed' WHERE id = ?",
                    (trade["id"],),
                )

            close_early_reversion_position(
                conn,
                strategy_version="v2",
                trade=trade,
                token_id="wrong-active-market-token",
                bid=0.35,
                exit_reason="STOP_LOSS",
                size_usdc=1.0,
                close_trade=_finalize,
            )
            conn.commit()

        mock_sell.assert_called_once()
        sell_order: ExitOrder = mock_sell.call_args.args[0]
        self.assertEqual(sell_order.token_id, "entry-token-no")


if __name__ == "__main__":
    unittest.main()
