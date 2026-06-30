"""Tests for fill audit resolution and sync."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import (
    connect,
    get_order_fill_audit,
    init_db,
    insert_order_intent,
    update_order_intent_status,
)
from bot.fill_audit import (
    ClobFillAdapter,
    FillPending,
    ResolvedFill,
    _compute_slippage,
    _parse_share_amount,
    ensure_submitted_snapshot,
    log_fill_pending,
    resolve_fill,
    sync_order_fill,
)


class FillAuditTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_submitted_intent(self, conn) -> str:
        key = "v2:NO_C:btc-updown-5m-test:NO:entry"
        insert_order_intent(
            conn,
            idempotency_key=key,
            trading_mode="live",
            strategy_version="v2",
            strategy_name="NO_C",
            market_slug="btc-updown-5m-test",
            side="NO",
            token_id="token-no",
            price=0.40,
            size_usdc=2.0,
            shares=5.0,
            status="pending",
        )
        update_order_intent_status(
            conn,
            key,
            status="submitted",
            clob_order_id="order-123",
        )
        conn.commit()
        return key

    def test_parse_share_amount_micro_units_and_fractional(self) -> None:
        self.assertAlmostEqual(_parse_share_amount("5000000"), 5.0)
        self.assertAlmostEqual(_parse_share_amount("41.666665"), 41.666665)
        self.assertAlmostEqual(_parse_share_amount("5000000.0"), 5.0)
        self.assertAlmostEqual(_parse_share_amount(5000000), 5.0)

    def test_resolve_fill_from_associated_trades(self) -> None:
        order = {
            "id": "order-123",
            "original_size": "5000000",
            "size_matched": "5000000",
            "price": "0.40",
            "status": "ORDER_STATUS_MATCHED",
            "associate_trades": ["trade-1"],
        }
        trades = [
            {
                "id": "trade-1",
                "taker_order_id": "order-123",
                "size": "5000000",
                "price": "0.38",
                "fee_rate_bps": "100",
            }
        ]
        resolved = resolve_fill(order, trades)
        self.assertIsInstance(resolved, ResolvedFill)
        assert isinstance(resolved, ResolvedFill)
        self.assertAlmostEqual(resolved.fill_shares, 5.0)
        self.assertAlmostEqual(resolved.average_fill_price, 0.38)
        self.assertAlmostEqual(resolved.fill_notional, 1.9)
        self.assertEqual(resolved.fill_status, "filled")
        self.assertGreater(resolved.fees, 0)

    def test_resolve_fill_uses_maker_order_not_trade_price(self) -> None:
        order = {
            "id": "order-maker",
            "original_size": "5666665",
            "size_matched": "5666665",
            "price": "0.37",
            "status": "ORDER_STATUS_MATCHED",
            "associate_trades": ["trade-1"],
        }
        trades = [
            {
                "id": "trade-1",
                "taker_order_id": "order-taker",
                "size": "5666665",
                "price": "0.62",
                "maker_orders": [
                    {
                        "order_id": "order-maker",
                        "matched_amount": "5666665",
                        "price": "0.37",
                        "fee_rate_bps": "0",
                    }
                ],
            }
        ]
        resolved = resolve_fill(order, trades)
        self.assertIsInstance(resolved, ResolvedFill)
        assert isinstance(resolved, ResolvedFill)
        self.assertAlmostEqual(resolved.average_fill_price, 0.37)
        self.assertAlmostEqual(resolved.fill_shares, 5.666665)

    def test_resolve_fill_pending_when_no_trades(self) -> None:
        order = {
            "id": "order-123",
            "original_size": "5000000",
            "size_matched": "0",
            "price": "0.62",
            "status": "ORDER_STATUS_LIVE",
            "associate_trades": [],
        }
        resolved = resolve_fill(order, [])
        self.assertIsInstance(resolved, FillPending)
        assert isinstance(resolved, FillPending)
        self.assertAlmostEqual(resolved.matched_shares, 0.0)
        self.assertEqual(resolved.order_status, "ORDER_STATUS_LIVE")

    def test_resolve_fill_pending_when_trades_have_zero_shares_for_order(self) -> None:
        order = {
            "id": "order-123",
            "original_size": "5000000",
            "size_matched": "2500000",
            "price": "0.40",
            "status": "ORDER_STATUS_LIVE",
            "associate_trades": ["trade-1"],
        }
        trades = [
            {
                "id": "trade-1",
                "taker_order_id": "other-order",
                "size": "2500000",
                "price": "0.62",
                "maker_orders": [],
            }
        ]
        resolved = resolve_fill(order, trades)
        self.assertIsInstance(resolved, FillPending)
        assert isinstance(resolved, FillPending)
        self.assertAlmostEqual(resolved.matched_shares, 2.5)

    def test_resolve_fill_fractional_maker_matched_amount(self) -> None:
        order = {
            "id": "order-maker",
            "original_size": "41666665",
            "size_matched": "41666665",
            "price": "0.37",
            "status": "ORDER_STATUS_MATCHED",
            "associate_trades": ["trade-1"],
        }
        trades = [
            {
                "id": "trade-1",
                "taker_order_id": "order-taker",
                "size": "41666665",
                "price": "0.62",
                "maker_orders": [
                    {
                        "order_id": "order-maker",
                        "matched_amount": "41.666665",
                        "price": "0.37",
                        "fee_rate_bps": "0",
                    }
                ],
            }
        ]
        resolved = resolve_fill(order, trades)
        self.assertIsInstance(resolved, ResolvedFill)
        assert isinstance(resolved, ResolvedFill)
        self.assertAlmostEqual(resolved.fill_shares, 41.666665)
        self.assertAlmostEqual(resolved.average_fill_price, 0.37)

    def test_resolve_fill_partial_from_fractional_trades(self) -> None:
        order = {
            "id": "order-123",
            "original_size": "8333333",
            "size_matched": "4166665",
            "price": "0.40",
            "status": "ORDER_STATUS_LIVE",
            "associate_trades": ["trade-1"],
        }
        trades = [
            {
                "id": "trade-1",
                "taker_order_id": "order-123",
                "size": "2500000",
                "price": "0.39",
                "fee_rate_bps": "0",
            }
        ]
        resolved = resolve_fill(order, trades)
        self.assertIsInstance(resolved, ResolvedFill)
        assert isinstance(resolved, ResolvedFill)
        self.assertAlmostEqual(resolved.fill_shares, 2.5)
        self.assertAlmostEqual(resolved.average_fill_price, 0.39)
        self.assertEqual(resolved.fill_status, "partial")

    def test_slippage_for_buy_and_sell(self) -> None:
        buy_diff, buy_slip = _compute_slippage(
            clob_side="BUY",
            submitted_price=0.40,
            fill_price=0.38,
        )
        self.assertAlmostEqual(buy_diff, -0.02)
        self.assertAlmostEqual(buy_slip, -5.0)

        sell_diff, sell_slip = _compute_slippage(
            clob_side="SELL",
            submitted_price=0.40,
            fill_price=0.35,
        )
        self.assertAlmostEqual(sell_diff, -0.05)
        self.assertAlmostEqual(sell_slip, 12.5)

    @mock.patch.dict(
        os.environ,
        {
            "TRADING_MODE": "live",
            "POLY_PRIVATE_KEY": "0x" + "11" * 32,
        },
        clear=False,
    )
    def test_sync_order_fill_logs_once(self) -> None:
        import importlib
        import bot.config as config
        import bot.fill_audit as fill_audit

        importlib.reload(config)
        importlib.reload(fill_audit)

        class FakeAdapter:
            def fetch_order(self, order_id: str):
                return {
                    "id": order_id,
                    "original_size": "5000000",
                    "size_matched": "5000000",
                    "price": "0.40",
                    "status": "ORDER_STATUS_MATCHED",
                    "associate_trades": ["trade-1"],
                }

            def fetch_trades_for_order(self, order: dict):
                return [
                    {
                        "id": "trade-1",
                        "taker_order_id": order["id"],
                        "size": "5000000",
                        "price": "0.38",
                        "fee_rate_bps": "0",
                    }
                ]

        with connect(self.db_path) as conn:
            key = self._seed_submitted_intent(conn)
            intent = conn.execute(
                "SELECT * FROM order_intents WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            with self.assertLogs("bot.fill_audit", level="INFO") as logs:
                self.assertTrue(
                    fill_audit.sync_order_fill(conn, intent, adapter=FakeAdapter())
                )
                self.assertFalse(
                    fill_audit.sync_order_fill(conn, intent, adapter=FakeAdapter())
                )
            conn.commit()
            audit = get_order_fill_audit(conn, key)

        self.assertEqual(sum(1 for line in logs.output if "FILL AUDIT" in line), 1)
        self.assertEqual(sum(1 for line in logs.output if "FILL PENDING" in line), 0)
        self.assertIsNotNone(audit)
        self.assertAlmostEqual(float(audit["average_fill_price"]), 0.38)
        self.assertAlmostEqual(float(audit["fill_shares"]), 5.0)

    @mock.patch.dict(
        os.environ,
        {
            "TRADING_MODE": "live",
            "POLY_PRIVATE_KEY": "0x" + "11" * 32,
        },
        clear=False,
    )
    def test_sync_order_fill_pending_no_audit(self) -> None:
        import importlib
        import bot.config as config
        import bot.fill_audit as fill_audit

        importlib.reload(config)
        importlib.reload(fill_audit)

        class FakeAdapter:
            def fetch_order(self, order_id: str):
                return {
                    "id": order_id,
                    "original_size": "5000000",
                    "size_matched": "0",
                    "price": "0.62",
                    "status": "ORDER_STATUS_LIVE",
                    "associate_trades": [],
                }

            def fetch_trades_for_order(self, order: dict):
                return []

        with connect(self.db_path) as conn:
            key = self._seed_submitted_intent(conn)
            intent = conn.execute(
                "SELECT * FROM order_intents WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            with self.assertLogs("bot.fill_audit", level="INFO") as logs:
                self.assertTrue(
                    fill_audit.sync_order_fill(conn, intent, adapter=FakeAdapter())
                )
                self.assertFalse(
                    fill_audit.sync_order_fill(conn, intent, adapter=FakeAdapter())
                )
            conn.commit()
            audit = get_order_fill_audit(conn, key)

        self.assertEqual(sum(1 for line in logs.output if "FILL PENDING" in line), 1)
        self.assertEqual(sum(1 for line in logs.output if "FILL AUDIT" in line), 0)
        self.assertIsNotNone(audit)
        self.assertIsNone(audit["fill_price"])
        self.assertIsNone(audit["average_fill_price"])
        self.assertIsNone(audit["slippage"])
        self.assertEqual(audit["fill_status"], "pending")


if __name__ == "__main__":
    unittest.main()
