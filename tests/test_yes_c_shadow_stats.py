"""Tests for YES_C shadow analytics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.database import close_yes_c_shadow_trade, connect, init_db, insert_early_reversion_v2_trade, insert_yes_c_shadow_trade
from bot.yes_c_shadow_stats import (
    format_strategy_comparison,
    format_yes_c_shadow_funnel,
    format_yes_c_shadow_report,
)


class YesCShadowStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _close_shadow(
        self,
        conn,
        trade_id: int,
        *,
        exit_price: float,
        pnl_percent: float,
        pnl_usdc: float,
    ) -> None:
        close_yes_c_shadow_trade(
            conn,
            trade_id,
            exit_price=exit_price,
            exit_reason="TIME_STOP",
            pnl_percent=pnl_percent,
            pnl_usdc=pnl_usdc,
            holding_time_seconds=60.0,
        )

    def test_yes_c_shadow_report_contains_required_sections(self) -> None:
        with connect(self.db_path) as conn:
            trade_id = insert_yes_c_shadow_trade(
                conn,
                market_slug="btc-updown-5m-1",
                window_start_ts=1,
                end_ts=301,
                side="YES",
                strategy_name="YES_C",
                entry_price=0.40,
                entry_ts=10,
            )
            self._close_shadow(conn, trade_id, exit_price=0.42, pnl_percent=5.0, pnl_usdc=0.10)
            trade_id = insert_yes_c_shadow_trade(
                conn,
                market_slug="btc-updown-5m-2",
                window_start_ts=302,
                end_ts=602,
                side="YES",
                strategy_name="YES_C",
                entry_price=0.40,
                entry_ts=320,
            )
            self._close_shadow(conn, trade_id, exit_price=0.36, pnl_percent=-10.0, pnl_usdc=-0.21)
            conn.commit()
            report = format_yes_c_shadow_report(conn)

        for fragment in (
            "YES_C SHADOW",
            "Trades: 2",
            "Wins: 1",
            "Losses: 1",
            "Win Rate: 50%",
            "Average PnL:",
            "BTC UP",
            "BTC DOWN",
            "Profit Factor:",
            "Average Holding Time:",
        ):
            self.assertIn(fragment, report)

    def test_strategy_comparison_table(self) -> None:
        with connect(self.db_path) as conn:
            shadow_id = insert_yes_c_shadow_trade(
                conn,
                market_slug="btc-updown-5m-shadow",
                window_start_ts=1,
                end_ts=301,
                side="YES",
                strategy_name="YES_C",
                entry_price=0.40,
                entry_ts=10,
            )
            self._close_shadow(conn, shadow_id, exit_price=0.44, pnl_percent=10.0, pnl_usdc=0.21)

            live_id = insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-live",
                window_start_ts=1,
                end_ts=301,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=10,
            )
            conn.execute(
                """
                UPDATE early_reversion_v2_trades
                SET status = 'closed',
                    exit_price = 0.38,
                    exit_reason = 'STOP_LOSS',
                    pnl_percent = -5.0,
                    pnl_usdc = -0.10,
                    holding_time_seconds = 45.0,
                    closed_at = datetime('now')
                WHERE id = ?
                """,
                (live_id,),
            )
            conn.commit()
            comparison = format_strategy_comparison(conn)

        self.assertIn("Strategy Comparison", comparison)
        self.assertIn("NO_C       LIVE", comparison)
        self.assertIn("YES_C      SHADOW", comparison)
        self.assertIn("V4         SHADOW", comparison)

    def test_yes_c_shadow_funnel_report(self) -> None:
        with connect(self.db_path) as conn:
            from bot.er_stats import record_entry_evaluation, record_entry_success, YES_C_SHADOW_VERSION

            record_entry_evaluation(
                conn,
                strategy_version=YES_C_SHADOW_VERSION,
                strategy_name="YES_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
                entry_window_sec=30,
                has_trade=False,
                risk=None,
                would_enter=True,
            )
            record_entry_success(conn, YES_C_SHADOW_VERSION, "YES_C")
            conn.commit()
            funnel = format_yes_c_shadow_funnel(conn)

        self.assertIn("YES_C SHADOW FUNNEL", funnel)
        self.assertIn("Проверок: 1", funnel)
        self.assertIn("Окно открыто: 1", funnel)
        self.assertIn("Цена прошла порог: 1", funnel)
        self.assertIn("Готова к входу: 1", funnel)
        self.assertIn("Виртуальных входов: 1", funnel)
