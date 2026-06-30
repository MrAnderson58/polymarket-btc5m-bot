"""Tests for STOP LOSS short-window recovery statistics."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
)
from bot.er_stop_loss_short_recovery_stats import (
    SETTLEMENT_BID_THRESHOLD,
    fetch_stop_loss_short_recovery_stats,
    format_stop_loss_short_recovery_summary,
)


class ErStopLossShortRecoveryStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _seed_checks(
        self,
        conn,
        *,
        market_slug: str,
        side: str,
        points: list[tuple[int, float]],
    ) -> None:
        for ts, bid in points:
            checked_at = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            yes_bid = bid if side == "YES" else 0.49
            no_bid = bid if side == "NO" else 0.49
            conn.execute(
                """
                INSERT INTO market_checks (
                    market_slug, seconds_remaining, strike_price, btc_price,
                    yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market_slug,
                    120.0,
                    100_000.0,
                    100_000.0,
                    yes_bid,
                    yes_bid + 0.01,
                    no_bid,
                    no_bid + 0.01,
                    None,
                    checked_at,
                ),
            )

    def _insert_stop_loss_trade(
        self,
        conn,
        *,
        slug: str,
        entry_price: float,
        exit_price: float,
        entry_ts: int,
        end_ts: int,
        holding_time_seconds: float,
        side: str = "NO",
    ) -> int:
        trade_id = insert_early_reversion_v2_trade(
            conn,
            market_slug=slug,
            window_start_ts=entry_ts,
            end_ts=end_ts,
            side=side,
            strategy_name="NO_C",
            entry_price=entry_price,
            entry_ts=entry_ts,
        )
        close_early_reversion_v2_trade(
            conn,
            trade_id,
            exit_price=exit_price,
            exit_reason="STOP_LOSS",
            pnl_percent=-10.0,
            pnl_usdc=-1.0,
            holding_time_seconds=holding_time_seconds,
        )
        return trade_id

    def test_short_recovery_windows_exclude_settlement(self) -> None:
        entry_ts = 1_700_000_000
        exit_ts = entry_ts + 30
        end_ts = exit_ts + 300
        slug = "btc-updown-5m-short"

        with connect(self.db_path) as conn:
            self._insert_stop_loss_trade(
                conn,
                slug=slug,
                entry_price=0.40,
                exit_price=0.36,
                entry_ts=entry_ts,
                end_ts=end_ts,
                holding_time_seconds=30.0,
            )
            self._seed_checks(
                conn,
                market_slug=slug,
                side="NO",
                points=[
                    (exit_ts, 0.36),
                    (exit_ts + 20, 0.39),
                    (exit_ts + 40, 0.44),
                    (exit_ts + 120, SETTLEMENT_BID_THRESHOLD),
                ],
            )
            conn.commit()
            stats = fetch_stop_loss_short_recovery_stats(conn)

        self.assertEqual(stats.analyzed, 1)
        trade = stats.trades[0]
        self.assertAlmostEqual(trade.max_bid_15s, 0.36)
        self.assertAlmostEqual(trade.max_bid_30s, 0.39)
        self.assertAlmostEqual(trade.max_bid_45s, 0.44)
        self.assertAlmostEqual(trade.max_bid_60s, 0.44)
        self.assertFalse(trade.recovered_entry_15s)
        self.assertTrue(trade.recovered_entry_60s)
        self.assertTrue(trade.recovered_trailing_60s)

        hold_60 = stats.alternative_holds[-1]
        self.assertAlmostEqual(hold_60.avg_pnl, 10.0)
        self.assertGreater(hold_60.net_profit, stats.actual_stop_loss_net_profit)

    def test_never_recovered_within_60s(self) -> None:
        entry_ts = 1_700_100_000
        exit_ts = entry_ts + 20
        end_ts = exit_ts + 120
        slug = "btc-updown-5m-no-short-recovery"

        with connect(self.db_path) as conn:
            self._insert_stop_loss_trade(
                conn,
                slug=slug,
                entry_price=0.40,
                exit_price=0.34,
                entry_ts=entry_ts,
                end_ts=end_ts,
                holding_time_seconds=20.0,
            )
            self._seed_checks(
                conn,
                market_slug=slug,
                side="NO",
                points=[
                    (exit_ts, 0.34),
                    (exit_ts + 10, 0.35),
                    (exit_ts + 40, 0.37),
                ],
            )
            conn.commit()
            stats = fetch_stop_loss_short_recovery_stats(conn)

        self.assertEqual(stats.never_recovered_60s_count, 1)
        self.assertEqual(stats.recovered_entry_60s_count, 0)

    def test_format_summary_contains_required_sections(self) -> None:
        entry_ts = 1_700_200_000
        exit_ts = entry_ts + 10
        end_ts = exit_ts + 90
        slug = "btc-updown-5m-short-format"

        with connect(self.db_path) as conn:
            self._insert_stop_loss_trade(
                conn,
                slug=slug,
                entry_price=0.38,
                exit_price=0.34,
                entry_ts=entry_ts,
                end_ts=end_ts,
                holding_time_seconds=10.0,
            )
            self._seed_checks(
                conn,
                market_slug=slug,
                side="NO",
                points=[(exit_ts, 0.34), (exit_ts + 5, 0.39)],
            )
            conn.commit()
            summary = format_stop_loss_short_recovery_summary(conn)

        self.assertIn("STOP LOSS SHORT RECOVERY", summary)
        self.assertIn("Recovered to Entry", summary)
        self.assertIn("15 sec:", summary)
        self.assertIn("60 sec:", summary)
        self.assertIn("Never recovered within 60s", summary)
        self.assertIn("ALTERNATIVE HOLD", summary)
        self.assertIn("Hold 15 sec", summary)
        self.assertIn("Hold 60 sec", summary)
        self.assertIn("Last 30 STOP_LOSS trades", summary)
        self.assertIn(slug, summary)


if __name__ == "__main__":
    unittest.main()
