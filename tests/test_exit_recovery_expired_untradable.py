"""Tests for expired + permanently untradable exit recovery."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import (
    connect,
    count_all_open_positions,
    init_db,
    insert_early_reversion_v2_trade,
    insert_order_intent,
    update_order_intent_status,
)
from bot.exit_recovery import EXIT_REASON_NO_POSITION, reconcile_stuck_exits
from bot.risk import check_can_open_position

_WINDOW_START = 1_700_000_000
_END_TS = 1_700_000_300
_EXPIRED_NOW = _END_TS + 100
_ACTIVE_NOW = _END_TS - 100


class ExitRecoveryExpiredUntradableTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRADING_MODE": "live",
                "LIVE_EXIT_ENABLED": "true",
                "MAX_OPEN_POSITIONS": "1",
                "POLY_PRIVATE_KEY": "0x" + "11" * 32,
            },
            clear=False,
        )
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.exit_recovery as exit_recovery
        import bot.risk as risk

        importlib.reload(config)
        importlib.reload(risk)
        importlib.reload(exit_recovery)
        self.exit_recovery = exit_recovery

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _seed_failed_exit(
        self,
        conn,
        *,
        error_message: str,
        end_ts: int = _END_TS,
    ) -> tuple[int, str]:
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
            window_start_ts=_WINDOW_START,
            end_ts=end_ts,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.40,
            entry_ts=_WINDOW_START + 10,
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

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=0.0)
    def test_expired_no_orderbook_closes_sqlite(self, _mock_balance: mock.MagicMock) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="exit blocked: token has no CLOB orderbook",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=_EXPIRED_NOW)
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

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=0.0)
    def test_expired_invalid_token_closes_sqlite(self, _mock_balance: mock.MagicMock) -> None:
        with connect(self.db_path) as conn:
            trade_id, _exit_key = self._seed_failed_exit(
                conn,
                error_message="PolyApiException: invalid token id",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=_EXPIRED_NOW)
            conn.commit()
            trade = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()

        self.assertEqual(reconciled, 1)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], EXIT_REASON_NO_POSITION)

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=2.5)
    def test_expired_balance_positive_closes_with_warning(
        self,
        _mock_balance: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="exit blocked: token has no CLOB orderbook",
            )
            with self.assertLogs("bot.exit_recovery", level="WARNING") as logs:
                reconciled = reconcile_stuck_exits(conn, now_ts=_EXPIRED_NOW)
            conn.commit()
            trade = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            exit_intent = conn.execute(
                "SELECT 1 FROM order_intents WHERE idempotency_key = ?",
                (exit_key,),
            ).fetchone()

        joined = "\n".join(logs.output)
        self.assertEqual(reconciled, 1)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], EXIT_REASON_NO_POSITION)
        self.assertIsNone(exit_intent)
        self.assertIn("RECOVERY WARNING", joined)
        self.assertIn("wallet_balance=2.5000", joined)
        self.assertIn("Manual redeem may be required.", joined)

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=2.5)
    def test_active_market_temporary_error_does_not_close_sqlite(
        self,
        _mock_balance: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="PolyApiException: Request exception!",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=_ACTIVE_NOW)
            conn.commit()
            trade = conn.execute(
                "SELECT status FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            exit_intent = conn.execute(
                "SELECT 1 FROM order_intents WHERE idempotency_key = ?",
                (exit_key,),
            ).fetchone()

        self.assertEqual(reconciled, 1)
        self.assertEqual(trade["status"], "open")
        self.assertIsNone(exit_intent)

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=2.5)
    def test_active_market_timeout_clears_intent_for_retry(
        self,
        _mock_balance: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="CLOB timeout while submitting SELL",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=_ACTIVE_NOW)
            conn.commit()
            trade = conn.execute(
                "SELECT status FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            exit_intent = conn.execute(
                "SELECT 1 FROM order_intents WHERE idempotency_key = ?",
                (exit_key,),
            ).fetchone()

        self.assertEqual(reconciled, 1)
        self.assertEqual(trade["status"], "open")
        self.assertIsNone(exit_intent)

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=2.5)
    def test_active_market_permanent_error_does_not_close_sqlite(
        self,
        _mock_balance: mock.MagicMock,
    ) -> None:
        with connect(self.db_path) as conn:
            trade_id, exit_key = self._seed_failed_exit(
                conn,
                error_message="exit blocked: token has no CLOB orderbook",
            )
            reconciled = reconcile_stuck_exits(conn, now_ts=_ACTIVE_NOW)
            conn.commit()
            trade = conn.execute(
                "SELECT status FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            exit_intent = conn.execute(
                "SELECT status FROM order_intents WHERE idempotency_key = ?",
                (exit_key,),
            ).fetchone()

        self.assertEqual(reconciled, 0)
        self.assertEqual(trade["status"], "open")
        self.assertEqual(exit_intent["status"], "failed")

    @mock.patch("bot.portfolio.approval.evaluate_live_approval")
    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=2.5)
    def test_recovery_no_position_frees_max_open_positions_slot(
        self,
        _mock_balance: mock.MagicMock,
        mock_approval: mock.MagicMock,
    ) -> None:
        from bot.portfolio.approval import LiveApprovalResult
        mock_approval.return_value = LiveApprovalResult(
            allowed=True,
            entry_price=0.40,
            ai_decision="ALLOW",
            ai_confidence_pct=77.0,
            brain="ALLOW",
            scientist="ALLOW",
            review="ALLOW",
            risk="LOW",
            blockers=(),
            summary="ok",
        )
        with connect(self.db_path) as conn:
            self._seed_failed_exit(
                conn,
                error_message="exit blocked: token has no CLOB orderbook",
            )
            self.assertEqual(count_all_open_positions(conn), 1)
            self.assertFalse(check_can_open_position(conn).allowed)

            reconciled = reconcile_stuck_exits(conn, now_ts=_EXPIRED_NOW)
            conn.commit()

            self.assertEqual(reconciled, 1)
            self.assertEqual(count_all_open_positions(conn), 0)
            self.assertTrue(check_can_open_position(conn).allowed)


if __name__ == "__main__":
    unittest.main()
