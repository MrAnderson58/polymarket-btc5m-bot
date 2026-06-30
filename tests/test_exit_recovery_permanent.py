"""Tests for exit recovery with permanent token/orderbook errors."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db, insert_early_reversion_v2_trade, insert_order_intent, update_order_intent_status
from bot.exit_recovery import EXIT_REASON_NO_POSITION, reconcile_stuck_exits


class ExitRecoveryPermanentErrorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRADING_MODE": "live",
                "LIVE_EXIT_ENABLED": "true",
                "POLY_PRIVATE_KEY": "0x" + "11" * 32,
            },
            clear=False,
        )
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.exit_recovery as exit_recovery

        importlib.reload(config)
        importlib.reload(exit_recovery)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _seed_failed_exit(self, conn, *, error_message: str) -> tuple[int, str]:
        insert_order_intent(
            conn,
            idempotency_key="v2:NO_C:btc-updown-5m-test:NO:entry",
            trading_mode="live",
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-test",
            side="NO",
            token_id="token-no",
            price=0.40,
            size_usdc=1.0,
            shares=2.5,
            status="submitted",
        )
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug="btc-updown-5m-test",
            window_start_ts=1_700_000_000,
            end_ts=1_700_000_300,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.40,
            entry_ts=1_700_000_010,
        )
        exit_key = "v2:NO_C:btc-updown-5m-test:NO:exit"
        insert_order_intent(
            conn,
            idempotency_key=exit_key,
            trading_mode="live",
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-test",
            side="NO",
            token_id="bad-token",
            price=0.35,
            size_usdc=0.875,
            shares=2.5,
            status="failed",
        )
        update_order_intent_status(
            conn,
            exit_key,
            status="failed",
            error_message=error_message,
        )
        conn.commit()
        return trade_id, exit_key

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=2.5)
    def test_permanent_invalid_token_closes_db_even_with_balance(
        self,
        _mock_balance: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="PolyApiException: invalid token id",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=1_700_000_400)
            conn.commit()
            trade = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            exit_intent = conn.execute(
                "SELECT 1 FROM order_intents WHERE idempotency_key = ?",
                (exit_key,),
            ).fetchone()

        self.assertEqual(reconciled, 1)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], EXIT_REASON_NO_POSITION)
        self.assertIsNone(exit_intent)

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=None)
    def test_permanent_orderbook_error_closes_when_balance_unavailable(
        self,
        _mock_balance: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="exit blocked: token has no CLOB orderbook",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=1_700_000_400)
            conn.commit()
            trade = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()

        self.assertEqual(reconciled, 1)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], EXIT_REASON_NO_POSITION)

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=0.0)
    def test_permanent_invalid_token_closes_when_no_balance(
        self,
        _mock_balance: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="exit blocked: token has no CLOB orderbook",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=1_700_000_400)
            conn.commit()
            trade = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            exit_intent = conn.execute(
                "SELECT 1 FROM order_intents WHERE idempotency_key = ?",
                (exit_key,),
            ).fetchone()

        self.assertEqual(reconciled, 1)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], EXIT_REASON_NO_POSITION)
        self.assertIsNone(exit_intent)


if __name__ == "__main__":
    unittest.main()
