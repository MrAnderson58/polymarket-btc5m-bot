"""Tests for BTC direction analytics."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
    insert_market_check,
)
from bot.er_btc_direction_stats import (
    BtcDirection,
    _classify_btc_direction,
    fetch_strategy_btc_direction_stats,
    format_btc_direction_summary,
)


class ErBtcDirectionStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {"ENABLED_STRATEGIES": "NO_C"},
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _insert_market_check_at(
        self,
        conn,
        *,
        market_slug: str,
        ts: int,
        btc_price: float,
    ) -> None:
        checked_at = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            INSERT INTO market_checks (
                market_slug, seconds_remaining, strike_price, btc_price,
                yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (market_slug, 200.0, 64000.0, btc_price, 0.5, 0.51, 0.49, 0.5, None, checked_at),
        )

    def _insert_closed_trade(
        self,
        conn,
        *,
        slug: str,
        entry_ts: int,
        holding_seconds: float,
        pnl_percent: float,
    ) -> None:
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug=slug,
            window_start_ts=entry_ts,
            end_ts=entry_ts + 300,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.40,
            entry_ts=entry_ts,
        )
        close_early_reversion_v2_trade(
            conn,
            trade_id,
            exit_price=0.44 if pnl_percent > 0 else 0.36,
            exit_reason="TRAILING_STOP",
            pnl_percent=pnl_percent,
            pnl_usdc=0.10,
            holding_time_seconds=holding_seconds,
        )

    def test_classify_btc_direction(self) -> None:
        self.assertEqual(_classify_btc_direction(25.0), BtcDirection.UP)
        self.assertEqual(_classify_btc_direction(-25.0), BtcDirection.DOWN)
        self.assertEqual(_classify_btc_direction(2.0), BtcDirection.FLAT)

    def test_strategy_stats_by_btc_direction(self) -> None:
        entry_ts = int(time.time())
        slug_up = "btc-updown-5m-up"
        slug_down = "btc-updown-5m-down"
        with connect(self.db_path) as conn:
            self._insert_market_check_at(conn, market_slug=slug_up, ts=entry_ts, btc_price=64000.0)
            self._insert_market_check_at(
                conn, market_slug=slug_up, ts=entry_ts + 30, btc_price=64030.0
            )
            self._insert_market_check_at(conn, market_slug=slug_down, ts=entry_ts, btc_price=64000.0)
            self._insert_market_check_at(
                conn, market_slug=slug_down, ts=entry_ts + 30, btc_price=63970.0
            )
            self._insert_closed_trade(
                conn, slug=slug_up, entry_ts=entry_ts, holding_seconds=30, pnl_percent=10.0
            )
            self._insert_closed_trade(
                conn, slug=slug_down, entry_ts=entry_ts + 1, holding_seconds=30, pnl_percent=-8.0
            )
            conn.commit()
            stats = fetch_strategy_btc_direction_stats(conn, "NO_C")

        up = stats.buckets[0]
        down = stats.buckets[1]
        flat = stats.buckets[2]
        self.assertEqual(up.direction, "BTC UP")
        self.assertEqual(up.trades, 1)
        self.assertEqual(up.win_rate, 1.0)
        self.assertAlmostEqual(down.trades, 1)
        self.assertEqual(down.win_rate, 0.0)
        self.assertEqual(flat.trades, 0)
        self.assertAlmostEqual(stats.avg_btc_move_profitable, 30.0)
        self.assertAlmostEqual(stats.avg_btc_move_losing, -30.0)

    def test_format_summary_contains_sections(self) -> None:
        entry_ts = int(time.time())
        slug = "btc-updown-5m-format"
        with connect(self.db_path) as conn:
            self._insert_market_check_at(conn, market_slug=slug, ts=entry_ts, btc_price=64000.0)
            self._insert_market_check_at(
                conn, market_slug=slug, ts=entry_ts + 20, btc_price=64020.0
            )
            self._insert_closed_trade(
                conn, slug=slug, entry_ts=entry_ts, holding_seconds=20, pnl_percent=5.0
            )
            conn.commit()
            summary = format_btc_direction_summary(conn)

        self.assertIn("BTC Direction Summary", summary)
        self.assertIn("NO_C", summary)
        self.assertIn("BTC UP", summary)
        self.assertIn("BTC DOWN", summary)
        self.assertIn("BTC FLAT", summary)
        self.assertIn("Average BTC move during profitable trades", summary)
        self.assertIn("Average BTC move during losing trades", summary)


if __name__ == "__main__":
    unittest.main()
