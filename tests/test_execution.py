"""Tests for execution idempotency and risk guards."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db, insert_early_reversion_v2_trade
from bot.execution import (
    EntryOrder,
    MAX_LIVE_ORDER_SIZE_USDC,
    _shares_for_size,
    _submit_live_buy,
    attempt_entry_open,
    build_idempotency_key,
)


class ExecutionRiskTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRADING_MODE": "paper",
                "MAX_OPEN_POSITIONS": "5",
                "MAX_DAILY_LOSS_USDC": "10",
            },
            clear=False,
        )
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.execution as execution
        import bot.risk as risk

        importlib.reload(config)
        importlib.reload(risk)
        importlib.reload(execution)
        self.config = config
        self.execution = execution
        self.risk = risk

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _entry_order(self) -> EntryOrder:
        return EntryOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-test",
            side="NO",
            token_id="token-no",
            price=0.38,
            size_usdc=1.0,
        )

    def _insert_v2(self, conn) -> int:
        now = 1_700_000_000
        return insert_early_reversion_v2_trade(
            conn,
            market_slug="btc-updown-5m-test",
            window_start_ts=now,
            end_ts=now + 300,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.38,
            entry_ts=now,
        )

    def test_idempotency_blocks_duplicate_entry(self) -> None:
        order = self._entry_order()
        with connect(self.db_path) as conn:
            with self.assertLogs("bot.execution_audit", level="INFO") as logs:
                first = self.execution.attempt_entry_open(
                    conn, order, insert_trade=lambda: self._insert_v2(conn)
                )
                second = self.execution.attempt_entry_open(
                    conn, order, insert_trade=lambda: self._insert_v2(conn)
                )
            conn.commit()
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertTrue(any("ATTEMPT ENTRY RESULT" in line for line in logs.output))
            self.assertTrue(any("ENTRY TRACE START" in line for line in logs.output))
            self.assertTrue(any("ENTRY TRACE SUBMIT" in line for line in logs.output))
            self.assertTrue(any("ENTRY TRACE SUCCESS" in line for line in logs.output))
            self.assertTrue(any("result=SUCCESS" in line for line in logs.output))
            self.assertTrue(any("ENTRY TRACE STOP" in line for line in logs.output))
            self.assertTrue(any("reason=duplicate" in line for line in logs.output))
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM early_reversion_v2_trades"
            ).fetchone()["c"]
            self.assertEqual(count, 1)

    def test_max_open_positions_blocks_second_entry(self) -> None:
        with mock.patch.dict(os.environ, {"MAX_OPEN_POSITIONS": "1"}, clear=False):
            import importlib
            import bot.config as config
            import bot.risk as risk
            import bot.execution as execution

            importlib.reload(config)
            importlib.reload(risk)
            importlib.reload(execution)
            self.execution = execution

        with connect(self.db_path) as conn:
            self._insert_v2(conn)
            conn.commit()

        order = EntryOrder(
            strategy_version="v2",
            strategy_name="YES_B",
            market_slug="btc-updown-5m-other",
            side="YES",
            token_id="token-yes",
            price=0.35,
            size_usdc=1.0,
        )

        with connect(self.db_path) as conn:
            with self.assertLogs("bot.execution_audit", level="INFO") as logs:
                opened = self.execution.attempt_entry_open(
                    conn,
                    order,
                    insert_trade=lambda: insert_early_reversion_v2_trade(
                        conn,
                        market_slug="btc-updown-5m-other",
                        window_start_ts=1_700_000_000,
                        end_ts=1_700_000_300,
                        side="YES",
                        strategy_name="YES_B",
                        entry_price=0.35,
                        entry_ts=1_700_000_010,
                    ),
                )
            conn.commit()
            self.assertFalse(opened)
            self.assertTrue(any("reason=max_open_positions" in line for line in logs.output))

    def test_dry_run_logs_without_live_submit(self) -> None:
        import importlib
        import bot.config as config
        import bot.execution as execution

        with mock.patch.dict(os.environ, {"TRADING_MODE": "dry_run"}, clear=False):
            importlib.reload(config)
            importlib.reload(execution)

        order = self._entry_order()
        with connect(self.db_path) as conn:
            with self.assertLogs(level="INFO") as logs:
                opened = execution.attempt_entry_open(
                    conn, order, insert_trade=lambda: self._insert_v2(conn)
                )
                conn.commit()
            self.assertTrue(opened)
            self.assertTrue(any("[DRY_RUN]" in msg for msg in logs.output))
            row = conn.execute(
                "SELECT status FROM order_intents WHERE idempotency_key = ?",
                (build_idempotency_key(order),),
            ).fetchone()
            self.assertEqual(row["status"], "dry_run")


class LiveEntryOrphanTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRADING_MODE": "live",
                "LIVE_EXIT_ENABLED": "false",
                "MAX_OPEN_POSITIONS": "5",
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

    def _entry_order(self) -> EntryOrder:
        return EntryOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-1700000000",
            side="NO",
            token_id="token-no",
            price=0.38,
            size_usdc=1.0,
        )

    @mock.patch("bot.execution.check_can_open_position")
    @mock.patch("bot.execution._submit_live_buy")
    def test_insert_trade_failure_creates_orphan_intent(
        self, mock_buy: mock.MagicMock, mock_risk: mock.MagicMock,
    ) -> None:
        from bot.risk import RiskCheckResult
        mock_risk.return_value = RiskCheckResult(allowed=True, reason="ok")
        mock_buy.return_value = (True, "clob-order-99", None)
        order = self._entry_order()
        key = build_idempotency_key(order)

        with connect(self.db_path) as conn:
            opened = self.execution.attempt_entry_open(
                conn,
                order,
                insert_trade=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
            )
            conn.commit()
            self.assertFalse(opened)
            intent = conn.execute(
                "SELECT status, clob_order_id, error_message FROM order_intents WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            trade_count = conn.execute(
                "SELECT COUNT(*) AS c FROM early_reversion_v2_trades"
            ).fetchone()["c"]

        self.assertEqual(intent["status"], "submitted")
        self.assertEqual(intent["clob_order_id"], "clob-order-99")
        self.assertTrue(intent["error_message"].startswith("unrecorded_trade:"))
        self.assertEqual(trade_count, 0)
        mock_buy.assert_called_once()

    @mock.patch("bot.execution.check_can_open_position")
    @mock.patch("bot.execution._submit_live_buy")
    def test_reconcile_unrecorded_entry_intent(
        self, mock_buy: mock.MagicMock, mock_risk: mock.MagicMock,
    ) -> None:
        from bot.risk import RiskCheckResult
        mock_risk.return_value = RiskCheckResult(allowed=True, reason="ok")
        mock_buy.return_value = (True, "clob-order-100", None)
        order = self._entry_order()
        key = build_idempotency_key(order)

        with connect(self.db_path) as conn:
            self.execution.attempt_entry_open(
                conn,
                order,
                insert_trade=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
            )
            conn.commit()
            recovered = self.execution.reconcile_unrecorded_entry_intents(conn)
            conn.commit()
            trade = conn.execute(
                "SELECT status, strategy_name FROM early_reversion_v2_trades"
            ).fetchone()
            intent = conn.execute(
                "SELECT error_message FROM order_intents WHERE idempotency_key = ?",
                (key,),
            ).fetchone()

        self.assertEqual(recovered, 1)
        self.assertEqual(trade["status"], "open")
        self.assertEqual(trade["strategy_name"], "NO_C")
        self.assertIsNone(intent["error_message"])


class OrderSizeTestCase(unittest.TestCase):
    def test_shares_enforces_min_order_size(self) -> None:
        with self.assertLogs("bot.execution", level="INFO") as logs:
            shares = _shares_for_size(1.0, 0.32)
        self.assertEqual(shares, 5.0)
        self.assertTrue(any("ORDER SIZE ADJUSTED" in msg for msg in logs.output))

    def test_shares_unchanged_when_above_minimum(self) -> None:
        shares = _shares_for_size(10.0, 0.5)
        self.assertEqual(shares, 20.0)


class LiveOrderPlacementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.execution as execution

        importlib.reload(config)
        importlib.reload(execution)
        self.execution = execution

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_order_type_gtc_is_not_partial_create_order_options(self) -> None:
        from py_clob_client_v2 import OrderType

        with self.assertRaises(AttributeError):
            _ = OrderType.GTC.tick_size

    def test_submit_live_buy_rejects_oversized_order(self) -> None:
        order = EntryOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-1700000000",
            side="NO",
            token_id="token-no",
            price=0.38,
            size_usdc=MAX_LIVE_ORDER_SIZE_USDC + 0.01,
        )

        with self.assertRaises(RuntimeError) as ctx:
            _submit_live_buy(order)

        self.assertIn("Safety check failed", str(ctx.exception))
        self.assertIn("2.5 USDC", str(ctx.exception))

    @mock.patch("bot.execution.get_authenticated_clob_client")
    def test_submit_live_buy_allows_max_size(self, mock_get_client: mock.MagicMock) -> None:
        client = mock.MagicMock()
        client.create_and_post_order.return_value = {"orderID": "clob-max"}
        mock_get_client.return_value = client

        order = EntryOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-1700000000",
            side="NO",
            token_id="token-no",
            price=0.38,
            size_usdc=MAX_LIVE_ORDER_SIZE_USDC,
        )

        ok, clob_order_id, error = _submit_live_buy(order)

        self.assertTrue(ok)
        self.assertEqual(clob_order_id, "clob-max")
        self.assertIsNone(error)
        client.create_and_post_order.assert_called_once()

    @mock.patch("bot.execution.get_authenticated_clob_client")
    def test_submit_live_buy_passes_order_type_as_keyword(
        self,
        mock_get_client: mock.MagicMock,
    ) -> None:
        from py_clob_client_v2 import OrderArgs, OrderType

        client = mock.MagicMock()
        client.create_and_post_order.return_value = {"orderID": "clob-1"}
        mock_get_client.return_value = client

        order = EntryOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-1700000000",
            side="NO",
            token_id="token-no",
            price=0.38,
            size_usdc=1.0,
        )

        ok, clob_order_id, error = self.execution._submit_live_buy(order)

        self.assertTrue(ok)
        self.assertEqual(clob_order_id, "clob-1")
        self.assertIsNone(error)
        client.create_and_post_order.assert_called_once()
        args, kwargs = client.create_and_post_order.call_args
        self.assertEqual(len(args), 1)
        self.assertIsInstance(args[0], OrderArgs)
        self.assertEqual(args[0].token_id, "token-no")
        self.assertEqual(kwargs.get("order_type"), OrderType.GTC)

    @mock.patch("bot.execution.get_authenticated_clob_client")
    def test_submit_live_sell_passes_order_type_as_keyword(
        self,
        mock_get_client: mock.MagicMock,
    ) -> None:
        from py_clob_client_v2 import OrderArgs, OrderType
        from bot.execution import ExitOrder

        client = mock.MagicMock()
        client.create_and_post_order.return_value = {"id": "clob-sell-1"}
        mock_get_client.return_value = client

        order = ExitOrder(
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-1700000000",
            side="NO",
            token_id="token-no",
            price=0.42,
            shares=2.5,
            exit_reason="TRAILING_STOP",
        )

        ok, clob_order_id, error = self.execution._submit_live_sell(order)

        self.assertTrue(ok)
        self.assertEqual(clob_order_id, "clob-sell-1")
        self.assertIsNone(error)
        args, kwargs = client.create_and_post_order.call_args
        self.assertEqual(len(args), 1)
        self.assertIsInstance(args[0], OrderArgs)
        self.assertEqual(kwargs.get("order_type"), OrderType.GTC)


if __name__ == "__main__":
    unittest.main()
