"""Tests for live exit order execution."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import (
    connect,
    init_db,
    insert_early_reversion_v2_trade,
    insert_order_intent,
)
from bot.execution import (
    ExitOrder,
    attempt_exit_close,
    build_exit_idempotency_key,
    close_early_reversion_position,
)


class LiveExitTestCase(unittest.TestCase):
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
        self._reload_modules()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _reload_modules(self) -> None:
        import importlib
        import bot.config as config
        import bot.execution as execution

        importlib.reload(config)
        importlib.reload(execution)
        self.execution = execution

    def _seed_open_trade(self, conn: sqlite3.Connection) -> sqlite3.Row:
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
        conn.commit()
        return conn.execute(
            "SELECT * FROM early_reversion_v2_trades WHERE id = ?",
            (trade_id,),
        ).fetchone()

    def _close_with_reason(
        self,
        conn: sqlite3.Connection,
        trade: sqlite3.Row,
        *,
        bid: float,
        exit_reason: str,
    ) -> bool:
        closed = {"done": False}

        def _finalize() -> None:
            conn.execute(
                """
                UPDATE early_reversion_v2_trades
                SET status = 'closed',
                    exit_price = ?,
                    exit_reason = ?,
                    pnl_percent = ?,
                    pnl_usdc = ?,
                    holding_time_seconds = 30,
                    closed_at = datetime('now')
                WHERE id = ?
                """,
                (bid, exit_reason, (bid - 0.40) / 0.40 * 100, 0.1, trade["id"]),
            )
            closed["done"] = True

        result = close_early_reversion_position(
            conn,
            strategy_version="v2",
            trade=trade,
            token_id="token-no",
            bid=bid,
            exit_reason=exit_reason,
            size_usdc=1.0,
            close_trade=_finalize,
        )
        conn.commit()
        self.assertTrue(closed["done"] or not result)
        return result

    @mock.patch("bot.execution._submit_live_sell")
    def test_live_exit_stop_loss(self, mock_sell: mock.MagicMock) -> None:
        mock_sell.return_value = (True, "sell-order-1", None)
        with connect(self.db_path) as conn:
            trade = self._seed_open_trade(conn)
            self.assertTrue(self._close_with_reason(conn, trade, bid=0.35, exit_reason="STOP_LOSS"))

        mock_sell.assert_called_once()
        sell_order: ExitOrder = mock_sell.call_args.args[0]
        self.assertEqual(sell_order.exit_reason, "STOP_LOSS")
        self.assertAlmostEqual(sell_order.shares, 2.5)

        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade["id"],),
            ).fetchone()
            intent = conn.execute(
                "SELECT status FROM order_intents WHERE idempotency_key = ?",
                ("v2:NO_C:btc-updown-5m-test:NO:exit",),
            ).fetchone()
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["exit_reason"], "STOP_LOSS")
        self.assertEqual(intent["status"], "submitted")

    @mock.patch("bot.execution._submit_live_sell")
    def test_live_exit_trailing_stop(self, mock_sell: mock.MagicMock) -> None:
        mock_sell.return_value = (True, "sell-order-2", None)
        with connect(self.db_path) as conn:
            trade = self._seed_open_trade(conn)
            self.assertTrue(
                self._close_with_reason(conn, trade, bid=0.42, exit_reason="TRAILING_STOP")
            )

        mock_sell.assert_called_once()
        self.assertEqual(mock_sell.call_args.args[0].exit_reason, "TRAILING_STOP")

    @mock.patch("bot.execution._submit_live_sell")
    def test_live_exit_time_stop(self, mock_sell: mock.MagicMock) -> None:
        mock_sell.return_value = (True, "sell-order-3", None)
        with connect(self.db_path) as conn:
            trade = self._seed_open_trade(conn)
            self.assertTrue(
                self._close_with_reason(conn, trade, bid=0.41, exit_reason="TIME_STOP")
            )

        mock_sell.assert_called_once()
        self.assertEqual(mock_sell.call_args.args[0].exit_reason, "TIME_STOP")

    @mock.patch("bot.execution._submit_live_sell")
    def test_restart_after_exit_order(self, mock_sell: mock.MagicMock) -> None:
        with connect(self.db_path) as conn:
            trade = self._seed_open_trade(conn)
            insert_order_intent(
                conn,
                idempotency_key="v2:NO_C:btc-updown-5m-test:NO:exit",
                trading_mode="live",
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                side="NO",
                token_id="token-no",
                price=0.35,
                size_usdc=0.875,
                shares=2.5,
                status="submitted",
            )
            conn.commit()

            exit_order = ExitOrder(
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                side="NO",
                token_id="token-no",
                price=0.35,
                shares=2.5,
                exit_reason="STOP_LOSS",
            )
            closed = {"done": False}

            def _finalize() -> None:
                conn.execute(
                    "UPDATE early_reversion_v2_trades SET status = 'closed' WHERE id = ?",
                    (trade["id"],),
                )
                closed["done"] = True

            self.assertTrue(
                attempt_exit_close(conn, exit_order, close_trade=_finalize)
            )
            conn.commit()

        mock_sell.assert_not_called()
        self.assertTrue(closed["done"])
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM early_reversion_v2_trades WHERE id = ?",
                (trade["id"],),
            ).fetchone()
        self.assertEqual(row["status"], "closed")

    @mock.patch("bot.execution._submit_live_sell")
    def test_live_exit_disabled_uses_paper_close(self, mock_sell: mock.MagicMock) -> None:
        with mock.patch.dict(os.environ, {"LIVE_EXIT_ENABLED": "false"}, clear=False):
            self._reload_modules()

        with connect(self.db_path) as conn:
            trade = self._seed_open_trade(conn)
            self.assertTrue(self._close_with_reason(conn, trade, bid=0.35, exit_reason="STOP_LOSS"))

        mock_sell.assert_not_called()
        with connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM order_intents WHERE idempotency_key LIKE '%:exit'").fetchone()["c"]
        self.assertEqual(count, 0)

    def test_pending_exit_intent_logs_duplicate_once(self) -> None:
        exit_key = "v2:NO_C:btc-updown-5m-test:NO:exit"
        exit_order = ExitOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-test",
            side="NO",
            token_id="token-no",
            price=0.35,
            shares=2.5,
            exit_reason="STOP_LOSS",
        )
        with connect(self.db_path) as conn:
            self._seed_open_trade(conn)
            insert_order_intent(
                conn,
                idempotency_key=exit_key,
                trading_mode="live",
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                side="NO",
                token_id="token-no",
                price=0.35,
                size_usdc=0.875,
                shares=2.5,
                status="pending",
            )
            conn.commit()
            self.execution._logged_exit_idempotency_skips.clear()

            with self.assertLogs("bot.execution", level="INFO") as captured:
                self.assertFalse(
                    self.execution.attempt_exit_close(conn, exit_order, close_trade=lambda: None)
                )
                self.assertFalse(
                    self.execution.attempt_exit_close(conn, exit_order, close_trade=lambda: None)
                )

        duplicate_lines = [
            line
            for line in captured.output
            if "Duplicate exit skipped (idempotency)" in line
        ]
        self.assertEqual(len(duplicate_lines), 1)


if __name__ == "__main__":
    unittest.main()
