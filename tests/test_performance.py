"""Tests for bot.performance report generation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
    insert_v4_shadow_trade,
)
from bot.performance import (
    build_version_summaries,
    fetch_closed_trades,
    format_report,
    group_by_strategy,
)


class PerformanceReportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_v2_trades(self, conn) -> None:
        trade_ids = []
        specs = [
            ("NO_C", 0.40, 0.44, "TRAILING_STOP", 10.0, 0.20),
            ("NO_C", 0.40, 0.36, "STOP_LOSS", -10.0, -0.20),
            ("YES_B", 0.35, 0.38, "TIME_STOP", 8.57, 0.17),
        ]
        for index, (strategy, entry, exit_price, reason, pnl_pct, pnl_usdc) in enumerate(specs):
            trade_id = insert_early_reversion_v2_trade(
                conn,
                market_slug=f"btc-updown-5m-{index}",
                window_start_ts=1_700_000_000 + index * 300,
                end_ts=1_700_000_300 + index * 300,
                side="NO" if strategy == "NO_C" else "YES",
                strategy_name=strategy,
                entry_price=entry,
                entry_ts=1_700_000_010 + index,
            )
            close_early_reversion_v2_trade(
                conn,
                trade_id,
                exit_price=exit_price,
                exit_reason=reason,
                pnl_percent=pnl_pct,
                pnl_usdc=pnl_usdc,
                holding_time_seconds=30.0 + index,
            )
            trade_ids.append(trade_id)
        conn.commit()
        return trade_ids

    def _seed_v4_trade(self, conn) -> None:
        trade_id = insert_v4_shadow_trade(
            conn,
            market_slug="btc-updown-5m-v4",
            window_start_ts=1_700_001_000,
            end_ts=1_700_001_300,
            side="YES",
            entry_price=0.42,
            entry_ts=1_700_001_010,
            entry_score=0.75,
            entry_probability=0.68,
            entry_reason="score_threshold",
        )
        conn.execute(
            """
            UPDATE v4_shadow_trades
            SET status = 'closed',
                exit_price = 0.48,
                exit_reason = 'TRAILING_STOP',
                holding_time_seconds = 45.0,
                trailing_activation_price = 0.45,
                highest_price = 0.50,
                max_profit_pct = 19.05,
                realized_profit_pct = 14.29,
                profit_left_on_table_pct = 4.0,
                closed_at = datetime('now')
            WHERE id = ?
            """,
            (trade_id,),
        )
        conn.commit()

    def test_fetch_and_group_trades(self) -> None:
        with connect(self.db_path) as conn:
            self._seed_v2_trades(conn)
            trades = fetch_closed_trades(conn)
            groups = group_by_strategy(trades)

        self.assertEqual(len(trades), 3)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].label, "V2 NO_C")
        self.assertEqual(len(groups[0].trades), 2)

    def test_format_report_contains_required_sections(self) -> None:
        with connect(self.db_path) as conn:
            self._seed_v2_trades(conn)
            self._seed_v4_trade(conn)
            report = format_report(fetch_closed_trades(conn))

        self.assertIn("Strategy: V2 NO_C", report)
        self.assertIn("PnL Distribution", report)
        self.assertIn("Exit Reasons", report)
        self.assertIn("Equity Curve", report)
        self.assertIn("Strategy Comparison", report)
        self.assertIn("V4 Shadow", report)
        self.assertIn("Average Score:", report)
        self.assertIn("TRAILING_STOP", report)
        self.assertIn("STOP_LOSS", report)

    def test_version_summary_metrics(self) -> None:
        with connect(self.db_path) as conn:
            self._seed_v2_trades(conn)
            trades = fetch_closed_trades(conn)
            summaries = build_version_summaries(trades)

        v2 = next(item for item in summaries if item.version == "V2")
        self.assertEqual(v2.trades, 3)
        self.assertAlmostEqual(v2.win_rate, 2 / 3, places=4)
        self.assertGreater(v2.profit_factor, 0)


if __name__ == "__main__":
    unittest.main()
