"""Tests for RECOVERY HEALTH block in ER SUMMARY."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import (
    close_early_reversion_v2_trade,
    connect,
    init_db,
    insert_early_reversion_v2_trade,
    insert_order_intent,
    update_order_intent_status,
)
from bot.er_stats import format_er_summary, record_entry_evaluation
from bot.exit_recovery import EXIT_REASON_NO_POSITION, reconcile_stuck_exits
from bot.recovery_health import (
    EVENT_BLOCKED_MAX_OPEN_POSITIONS,
    EVENT_RECOVERY_ACTION,
    fetch_recovery_health_stats,
    format_recovery_health_block,
    record_health_event,
)
from bot.risk import RiskCheckResult, check_can_open_position


class RecoveryHealthTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "ENABLED_STRATEGIES": "NO_C",
                "TRADING_MODE": "live",
                "LIVE_EXIT_ENABLED": "true",
                "MAX_OPEN_POSITIONS": "1",
                "POLY_PRIVATE_KEY": "0x" + "11" * 32,
            },
            clear=False,
        )
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.er_stats as er_stats
        import bot.exit_recovery as exit_recovery
        import bot.recovery_health as recovery_health
        import bot.risk as risk

        importlib.reload(config)
        importlib.reload(risk)
        importlib.reload(recovery_health)
        importlib.reload(exit_recovery)
        importlib.reload(er_stats)
        self.er_stats = er_stats

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_format_recovery_health_block(self) -> None:
        with connect(self.db_path) as conn:
            block = format_recovery_health_block(fetch_recovery_health_stats(conn))
        self.assertIn("RECOVERY HEALTH", block)
        self.assertIn("Open trades: 0", block)
        self.assertIn("Expired open trades: 0", block)
        self.assertIn("Failed exit intents: 0", block)
        self.assertIn("Blocked by MAX_OPEN_POSITIONS (1h): 0", block)
        self.assertIn("Recovery actions (1h): 0", block)

    def test_er_summary_includes_recovery_health(self) -> None:
        with connect(self.db_path) as conn:
            summary = format_er_summary(conn)
        self.assertIn("RECOVERY HEALTH", summary)
        self.assertIn("Expired open trades: 0", summary)

    def test_expired_open_trade_is_visible(self) -> None:
        now = int(time.time())
        with connect(self.db_path) as conn:
            insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-expired",
                window_start_ts=now - 600,
                end_ts=now - 60,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=now - 500,
            )
            conn.commit()
            stats = fetch_recovery_health_stats(conn, now_ts=now)

        self.assertEqual(stats.open_trades, 1)
        self.assertEqual(stats.expired_open_trades, 1)

    def test_failed_exit_intent_is_counted(self) -> None:
        with connect(self.db_path) as conn:
            insert_order_intent(
                conn,
                idempotency_key="v2:NO_C:btc-updown-5m-test:NO:exit",
                trading_mode="live",
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                side="NO",
                token_id="token-no",
                price=0.35,
                size_usdc=1.0,
                shares=2.5,
                status="failed",
            )
            update_order_intent_status(
                conn,
                "v2:NO_C:btc-updown-5m-test:NO:exit",
                status="failed",
                error_message="exit blocked: token has no CLOB orderbook",
            )
            conn.commit()
            stats = fetch_recovery_health_stats(conn)

        self.assertEqual(stats.failed_exit_intents, 1)

    def test_blocked_max_open_positions_recorded_in_last_hour(self) -> None:
        now = int(time.time())
        with connect(self.db_path) as conn:
            record_entry_evaluation(
                conn,
                strategy_version="v2",
                strategy_name="NO_C",
                ask=0.38,
                entry_threshold=0.40,
                seconds_open=12,
                entry_window_sec=120,
                has_trade=False,
                risk=RiskCheckResult(
                    allowed=False,
                    reason="MAX_OPEN_POSITIONS reached (1/1)",
                ),
                would_enter=False,
                eval_ts=now,
            )
            conn.commit()
            stats = fetch_recovery_health_stats(conn, now_ts=now)

        self.assertEqual(stats.blocked_max_open_positions_1h, 1)

    @mock.patch("bot.exit_recovery.get_conditional_token_balance_shares", return_value=0.0)
    def test_recovery_action_counted_and_frees_slot(self, _mock_balance: mock.MagicMock) -> None:
        end_ts = int(time.time()) - 60
        now = int(time.time())
        with connect(self.db_path) as conn:
            insert_order_intent(
                conn,
                idempotency_key="v2:NO_C:btc-updown-5m-test:NO:entry",
                trading_mode="live",
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                side="NO",
                token_id="token-no",
                price=0.40,
                size_usdc=1.0,
                shares=2.5,
                status="submitted",
            )
            trade_id = insert_early_reversion_v2_trade(
                conn,
                market_slug="btc-updown-5m-test",
                window_start_ts=end_ts - 300,
                end_ts=end_ts,
                side="NO",
                strategy_name="NO_C",
                entry_price=0.40,
                entry_ts=end_ts - 200,
            )
            exit_key = "v2:NO_C:btc-updown-5m-test:NO:exit"
            insert_order_intent(
                conn,
                idempotency_key=exit_key,
                trading_mode="live",
                strategy_version="v2",
                strategy_name="NO_C",
                market_slug="btc-updown-5m-test",
                side="NO",
                token_id="bad-token",
                price=0.35,
                size_usdc=0.875,
                shares=2.5,
                status="failed",
            )
            update_order_intent_status(
                conn,
                exit_key,
                status="failed",
                error_message="exit blocked: token has no CLOB orderbook",
            )
            conn.commit()

            self.assertFalse(check_can_open_position(conn).allowed)
            reconcile_stuck_exits(conn, now_ts=now)
            conn.commit()

            stats = fetch_recovery_health_stats(conn, now_ts=now)
            trade = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()

        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_reason"], EXIT_REASON_NO_POSITION)
        self.assertEqual(stats.open_trades, 0)
        self.assertGreaterEqual(stats.recovery_actions_1h, 1)

        with connect(self.db_path) as conn:
            self.assertTrue(check_can_open_position(conn).allowed)

    def test_record_health_event_helper(self) -> None:
        now = int(time.time())
        with connect(self.db_path) as conn:
            record_health_event(conn, EVENT_RECOVERY_ACTION, event_ts=now)
            record_health_event(conn, EVENT_BLOCKED_MAX_OPEN_POSITIONS, event_ts=now)
            conn.commit()
            stats = fetch_recovery_health_stats(conn, now_ts=now + 1)

        self.assertEqual(stats.recovery_actions_1h, 1)
        self.assertEqual(stats.blocked_max_open_positions_1h, 1)


if __name__ == "__main__":
    unittest.main()
