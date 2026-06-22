"""Tests for execution idempotency and risk guards."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db, insert_early_reversion_v2_trade
from bot.execution import EntryOrder, attempt_entry_open, build_idempotency_key


class ExecutionRiskTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRADING_MODE": "paper",
                "MAX_OPEN_POSITIONS": "1",
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
            first = self.execution.attempt_entry_open(
                conn, order, insert_trade=lambda: self._insert_v2(conn)
            )
            second = self.execution.attempt_entry_open(
                conn, order, insert_trade=lambda: self._insert_v2(conn)
            )
            conn.commit()
            self.assertTrue(first)
            self.assertFalse(second)
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM early_reversion_v2_trades"
            ).fetchone()["c"]
            self.assertEqual(count, 1)

    def test_max_open_positions_blocks_second_entry(self) -> None:
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

    def test_dry_run_logs_without_live_submit(self) -> None:
        import importlib
        import bot.config as config
        import bot.execution as execution

        with mock.patch.dict(os.environ, {"TRADING_MODE": "dry_run"}, clear=False):
            importlib.reload(config)
            importlib.reload(execution)

        order = self._entry_order()
        with connect(self.db_path) as conn:
            with self.assertLogs("bot.execution", level="INFO") as logs:
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


if __name__ == "__main__":
    unittest.main()
