"""Tests for Early Reversion trailing stop exit modes."""

from __future__ import annotations

import importlib
import os
import sqlite3
import unittest
from unittest import mock

import bot.config as config
import bot.er_trailing_stop as er_trailing_stop


def _trade_row(**overrides) -> sqlite3.Row:
    columns = {
        "max_price_seen": 0.38,
        "trailing_active": 0,
        "highest_price": None,
        "trailing_activation_price": None,
        "trailing_enabled": 1,
    }
    columns.update(overrides)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    keys = list(columns.keys())
    conn.execute(
        f"CREATE TABLE t ({', '.join(f'{k} REAL' for k in keys)})"
    )
    conn.execute(
        f"INSERT INTO t ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
        [columns[k] for k in keys],
    )
    return conn.execute("SELECT * FROM t").fetchone()


class ErTrailingStopTestCase(unittest.TestCase):
    def _reload(self, **env: str) -> None:
        with mock.patch.dict(os.environ, env, clear=False):
            importlib.reload(config)
            importlib.reload(er_trailing_stop)

    def _resolve(self, **kwargs):
        defaults = {
            "entry_price": 0.38,
            "bid": 0.40,
            "max_price_seen": 0.40,
            "seconds_in_trade": 60,
            "grace_sec": 30,
            "stop_loss_pct": -10,
            "time_stop_sec": 90,
            "legacy_trailing_pct": 5,
            "trailing_active": False,
            "highest_after_activation": None,
        }
        defaults.update(kwargs)
        return er_trailing_stop.resolve_er_exit_reason(**defaults)

    def test_trailing_not_active_before_activation_profit(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAILING_ACTIVATION_PROFIT="0.03",
            TRAILING_OFFSET="0.01",
        )
        trade = _trade_row(max_price_seen=0.40)
        tick = er_trailing_stop.process_trailing_tick(trade, 0.38, 0.40)
        self.assertFalse(tick.snapshot.trailing_active)
        self.assertIsNone(
            self._resolve(
                bid=0.40,
                max_price_seen=0.40,
                trailing_active=False,
            )
        )

    def test_trailing_activates_at_exact_activation_profit(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAILING_ACTIVATION_PROFIT="0.03",
            TRAILING_OFFSET="0.01",
        )
        trade = _trade_row(max_price_seen=0.41)
        tick = er_trailing_stop.process_trailing_tick(trade, 0.38, 0.41)
        self.assertTrue(tick.snapshot.trailing_active)
        self.assertAlmostEqual(tick.snapshot.trailing_stop_price, 0.40)
        self.assertAlmostEqual(tick.trailing_activation_price, 0.41)
        self.assertIsNone(
            self._resolve(
                bid=0.41,
                max_price_seen=0.41,
                trailing_active=True,
                highest_after_activation=0.41,
            )
        )

    def test_trailing_stop_always_one_cent_below_max(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAILING_ACTIVATION_PROFIT="0.03",
            TRAILING_OFFSET="0.01",
        )
        trade = _trade_row(
            max_price_seen=0.41,
            trailing_active=1,
            highest_price=0.41,
            trailing_activation_price=0.41,
        )
        tick = er_trailing_stop.process_trailing_tick(trade, 0.38, 0.46)
        self.assertAlmostEqual(tick.snapshot.highest_bid, 0.46)
        self.assertAlmostEqual(tick.snapshot.trailing_stop_price, 0.45)

    def test_trailing_updates_highest_bid_and_stop(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAILING_ACTIVATION_PROFIT="0.03",
            TRAILING_OFFSET="0.01",
        )
        first_trade = _trade_row(max_price_seen=0.41)
        first = er_trailing_stop.process_trailing_tick(first_trade, 0.38, 0.41)
        second_trade = _trade_row(
            max_price_seen=first.peak_bid,
            trailing_active=int(first.snapshot.trailing_active),
            highest_price=first.highest_price,
            trailing_activation_price=first.trailing_activation_price,
        )
        second = er_trailing_stop.process_trailing_tick(second_trade, 0.38, 0.46)
        self.assertAlmostEqual(second.snapshot.highest_bid, 0.46)
        self.assertAlmostEqual(second.snapshot.trailing_stop_price, 0.45)

    def test_trailing_exits_on_stop_touch(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAILING_ACTIVATION_PROFIT="0.03",
            TRAILING_OFFSET="0.01",
        )
        reason = self._resolve(
            bid=0.45,
            max_price_seen=0.46,
            trailing_active=True,
            highest_after_activation=0.46,
        )
        self.assertEqual(reason, "TRAILING_STOP")

    def test_trailing_profit_statistics(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAILING_ACTIVATION_PROFIT="0.03",
            TRAILING_OFFSET="0.01",
        )
        stats = er_trailing_stop.compute_trailing_close_stats(
            0.38,
            0.45,
            activation_price_value=0.41,
            highest_price=0.46,
        )
        self.assertAlmostEqual(stats.max_profit_pct, 21.052631578947368, places=4)
        self.assertAlmostEqual(stats.realized_profit_pct, 18.421052631578945, places=4)
        self.assertAlmostEqual(stats.profit_left_on_table_pct, 2.631578947368421, places=4)

    def test_time_stop_before_trailing_activation(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAILING_ACTIVATION_PROFIT="0.03",
            TRAILING_OFFSET="0.01",
        )
        reason = self._resolve(
            bid=0.39,
            max_price_seen=0.39,
            seconds_in_trade=95,
            trailing_active=False,
        )
        self.assertEqual(reason, "TIME_STOP")

    def test_fixed_mode_uses_legacy_percent_trailing(self) -> None:
        self._reload(EXIT_MODE="fixed", ENABLE_TRAILING_STOP="true")
        trade = _trade_row(max_price_seen=0.40)
        tick = er_trailing_stop.process_trailing_tick(trade, 0.38, 0.44)
        self.assertFalse(tick.snapshot.trailing_active)
        reason = self._resolve(
            entry_price=0.40,
            bid=0.37,
            max_price_seen=0.40,
            seconds_in_trade=60,
        )
        self.assertEqual(reason, "TRAILING_STOP")

    def test_stop_loss_before_trailing_activation(self) -> None:
        self._reload(EXIT_MODE="trailing", ENABLE_TRAILING_STOP="true")
        reason = self._resolve(
            entry_price=0.40,
            bid=0.35,
            max_price_seen=0.40,
            seconds_in_trade=60,
            trailing_active=False,
        )
        self.assertEqual(reason, "STOP_LOSS")

    def test_trailing_disabled_falls_back_to_fixed_mode(self) -> None:
        self._reload(EXIT_MODE="trailing", ENABLE_TRAILING_STOP="false")
        trade = _trade_row(max_price_seen=0.44)
        tick = er_trailing_stop.process_trailing_tick(trade, 0.38, 0.44)
        self.assertFalse(tick.snapshot.trailing_active)
        reason = self._resolve(
            entry_price=0.40,
            bid=0.37,
            max_price_seen=0.40,
            seconds_in_trade=60,
        )
        self.assertEqual(reason, "TRAILING_STOP")

    def test_legacy_env_aliases(self) -> None:
        self._reload(
            EXIT_MODE="trailing",
            ENABLE_TRAILING_STOP="true",
            TRAIL_ACTIVATION_DELTA="0.03",
            TRAIL_STOP_DELTA="0.01",
        )
        self.assertAlmostEqual(er_trailing_stop.TRAILING_ACTIVATION_PROFIT, 0.03)
        self.assertAlmostEqual(er_trailing_stop.TRAILING_OFFSET, 0.01)


if __name__ == "__main__":
    unittest.main()
