"""Tests for Trading AI Optimizer v1."""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
)
from bot.optimizer.builder import build_optimizer_report
from bot.optimizer.dataset import load_trade_features, rebuild_trade_features
from bot.optimizer.replay import StrategyParams, TradeReplay, simulate_trade


class OptimizerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed(self, conn) -> int:
        now = int(time.time())
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug="btc-updown-5m-opt",
            window_start_ts=now - 120,
            end_ts=now + 180,
            side="NO",
            strategy_name="NO_C",
            entry_price=0.38,
            entry_ts=now - 60,
        )
        close_early_reversion_v2_trade(
            conn,
            trade_id,
            exit_price=0.42,
            exit_reason="TRAILING_STOP",
            pnl_percent=10.0,
            pnl_usdc=0.2,
            holding_time_seconds=40.0,
        )
        checked_at = datetime.fromtimestamp(now, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        for offset, bid in ((0, 0.38), (20, 0.41), (40, 0.42)):
            conn.execute(
                """
                INSERT INTO market_checks (
                    market_slug, seconds_remaining, strike_price, btc_price,
                    yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime(?, 'unixepoch'))
                """,
                (
                    "btc-updown-5m-opt",
                    200.0 - offset,
                    100_000.0,
                    100_000.0 + offset,
                    0.5,
                    0.51,
                    bid,
                    bid + 0.01,
                    None,
                    now - 60 + offset,
                ),
            )
        return trade_id

    def test_rebuild_trade_features(self) -> None:
        with connect(self.db_path) as conn:
            self._seed(conn)
            conn.commit()
            n = rebuild_trade_features(conn)
            conn.commit()
            rows = load_trade_features(conn)
        self.assertEqual(n, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["is_win"]), 1)

    def test_simulate_trade(self) -> None:
        trade = TradeReplay(
            trade_id=1,
            entry_price=0.38,
            entry_ts=0,
            actual_pnl=10.0,
            btc_move_30s=2.0,
            bids=[(0, 0.38), (20, 0.41), (40, 0.42)],
        )
        params = StrategyParams(
            max_entry=0.40,
            stop_pct=-20.0,
            trailing_activation=0.03,
            trailing_distance=0.01,
            time_stop_sec=90,
            btc_filter_usd=30.0,
        )
        pnl, kind = simulate_trade(trade, params)
        self.assertIsNotNone(pnl)
        self.assertIn(kind, ("TRAILING", "TIME", "HOLD"))

    def test_build_optimizer_report_quick(self) -> None:
        with connect(self.db_path) as conn:
            self._seed(conn)
            conn.commit()
            report = build_optimizer_report(conn, full_grid=False)
        self.assertIn("parameter_optimizer", report)
        self.assertIn("recommendations", report)
        self.assertLessEqual(len(report["recommendations"]), 5)


if __name__ == "__main__":
    unittest.main()
