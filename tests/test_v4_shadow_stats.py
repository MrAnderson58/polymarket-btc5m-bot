"""Tests for V4 shadow analytics."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.database import (
    close_v4_shadow_trade,
    connect,
    init_db,
    insert_v4_shadow_trade,
)
from bot.v4_shadow_stats import format_v4_shadow_report
from bot.yes_c_shadow_stats import format_strategy_comparison


class V4ShadowStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_btc_checks(self, conn, *, market_slug: str, entry_ts: int, exit_ts: int, entry_btc: float, exit_btc: float) -> None:
        for ts, btc_price in ((entry_ts, entry_btc), (exit_ts, exit_btc)):
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

    def test_v4_shadow_report_contains_required_sections(self) -> None:
        with connect(self.db_path) as conn:
            trade_id = insert_v4_shadow_trade(
                conn,
                market_slug="btc-updown-5m-1",
                window_start_ts=1,
                end_ts=301,
                side="YES",
                entry_price=0.40,
                entry_ts=10,
                entry_score=9.0,
                entry_probability=0.75,
                entry_reason="score_threshold",
            )
            close_v4_shadow_trade(
                conn,
                trade_id,
                exit_price=0.44,
                exit_reason="TRAILING_STOP",
                holding_time_seconds=60.0,
                realized_profit_pct=10.0,
            )
            trade_id = insert_v4_shadow_trade(
                conn,
                market_slug="btc-updown-5m-2",
                window_start_ts=302,
                end_ts=602,
                side="NO",
                entry_price=0.40,
                entry_ts=320,
                entry_score=8.5,
                entry_probability=0.72,
                entry_reason="score_threshold",
            )
            close_v4_shadow_trade(
                conn,
                trade_id,
                exit_price=0.36,
                exit_reason="TIME_STOP",
                holding_time_seconds=90.0,
                realized_profit_pct=-10.0,
            )
            insert_v4_shadow_trade(
                conn,
                market_slug="btc-updown-5m-open",
                window_start_ts=603,
                end_ts=903,
                side="YES",
                entry_price=0.42,
                entry_ts=610,
                entry_score=8.0,
                entry_probability=0.70,
                entry_reason="score_threshold",
            )
            self._seed_btc_checks(
                conn,
                market_slug="btc-updown-5m-1",
                entry_ts=10,
                exit_ts=70,
                entry_btc=100.0,
                exit_btc=110.0,
            )
            self._seed_btc_checks(
                conn,
                market_slug="btc-updown-5m-2",
                entry_ts=320,
                exit_ts=410,
                entry_btc=100.0,
                exit_btc=90.0,
            )
            conn.commit()
            report = format_v4_shadow_report(conn)

        for fragment in (
            "V4 SHADOW",
            "Trades: 3",
            "Closed Trades: 2",
            "Wins: 1",
            "Losses: 1",
            "Win Rate: 50%",
            "Average PnL:",
            "Profit Factor:",
            "Average Holding Time:",
            "Average Entry Price:",
            "Average Exit Price:",
            "Trailing Stop exits: 1",
            "Stop Loss exits: 0",
            "Time Stop exits: 1",
            "BTC UP",
            "BTC DOWN",
            "BTC FLAT",
        ):
            self.assertIn(fragment, report)

    def test_strategy_comparison_includes_v4_row(self) -> None:
        with connect(self.db_path) as conn:
            trade_id = insert_v4_shadow_trade(
                conn,
                market_slug="btc-updown-5m-v4",
                window_start_ts=1,
                end_ts=301,
                side="YES",
                entry_price=0.40,
                entry_ts=10,
                entry_score=9.0,
                entry_probability=0.75,
                entry_reason="score_threshold",
            )
            close_v4_shadow_trade(
                conn,
                trade_id,
                exit_price=0.44,
                exit_reason="TRAILING_STOP",
                holding_time_seconds=60.0,
                realized_profit_pct=10.0,
            )
            conn.commit()
            comparison = format_strategy_comparison(conn)

        self.assertIn("Strategy Comparison", comparison)
        self.assertIn("V4         SHADOW", comparison)


if __name__ == "__main__":
    unittest.main()
