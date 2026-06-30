"""Tests for per-version ENABLE_V* flags in the main loop."""

from __future__ import annotations

import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.market_scanner import Btc5mMarket, TokenQuotes


def _current_window_start(now_ts: int) -> int:
    return (now_ts // 300) * 300


def _make_market(*, window_start: int) -> Btc5mMarket:
    return Btc5mMarket(
        slug=f"btc-updown-5m-{window_start}",
        title="BTC 5m test",
        condition_id="cond-test",
        window_start_ts=window_start,
        end_ts=window_start + 300,
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_outcome="Up",
        no_outcome="Down",
        yes_quotes=TokenQuotes("yes-token", 0.55, 0.57),
        no_quotes=TokenQuotes("no-token", 0.38, 0.39),
    )


def _no_c_quotes() -> dict[str, float | None]:
    return {
        "yes_bid": 0.61,
        "yes_ask": 0.63,
        "no_bid": 0.37,
        "no_ask": 0.39,
    }


class VersionEnableFlagsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _reload(self):
        os.environ["DATABASE_PATH"] = str(self.db_path)
        import bot.config as config
        import bot.database as database
        import bot.execution as execution
        import bot.early_reversion as er_v1
        import bot.early_reversion_v2 as er_v2
        import bot.early_reversion_v25 as er_v25
        import bot.early_reversion_v3 as er_v3
        import bot.main as main

        importlib.reload(config)
        importlib.reload(database)
        importlib.reload(execution)
        importlib.reload(er_v1)
        importlib.reload(er_v2)
        importlib.reload(er_v25)
        importlib.reload(er_v3)
        importlib.reload(main)
        return config, main

    def _apply_v2_only_env(self) -> None:
        os.environ.update(
            {
                "DATABASE_PATH": str(self.db_path),
                "TRADING_MODE": "paper",
                "LIVE_EXIT_ENABLED": "false",
                "ENABLE_V1": "false",
                "ENABLE_V2": "true",
                "ENABLE_V25": "false",
                "ENABLE_V3": "false",
                "ENABLE_LATE_WINDOW": "false",
                "ENABLED_STRATEGIES": "NO_C",
            }
        )

    def test_disabled_versions_not_called_in_cycle(self) -> None:
        self._apply_v2_only_env()
        _, main = self._reload()

        now_ts = int(time.time())
        window_start = _current_window_start(now_ts)
        market = _make_market(window_start=window_start)
        quotes = _no_c_quotes()

        with (
            mock.patch.object(main, "get_current_btc_price", return_value=100_000.0),
            mock.patch.object(main, "process_early_reversion") as process_v1,
            mock.patch.object(main, "process_early_reversion_v2", return_value=None) as process_v2,
            mock.patch.object(main, "process_early_reversion_v25") as process_v25,
            mock.patch.object(main, "close_due_early_reversion_trades") as close_v1,
            mock.patch.object(main, "close_due_early_reversion_v2_trades", return_value=0) as close_v2,
            mock.patch.object(main, "close_due_early_reversion_v25_trades") as close_v25,
            mock.patch.object(main, "find_active_btc_5m_market", return_value=market),
            mock.patch.object(main, "get_strike_price", return_value=99_900.0),
            mock.patch.object(main, "get_best_bid_ask", return_value=quotes),
            mock.patch.object(main, "evaluate", return_value=None),
        ):
            main._cycle()

        process_v1.assert_not_called()
        process_v25.assert_not_called()
        process_v2.assert_called_once()
        close_v1.assert_not_called()
        close_v25.assert_not_called()
        close_v2.assert_called_once()

    def test_disabled_v3_not_called_in_v3_cycle(self) -> None:
        self._apply_v2_only_env()
        _, main = self._reload()

        with (
            mock.patch.object(main, "close_due_early_reversion_v3_trades") as close_v3,
            mock.patch.object(main, "process_early_reversion_v3") as process_v3,
            mock.patch.object(main, "find_active_btc_5m_market", return_value=None),
            mock.patch.object(main, "run_startup_recovery"),
            mock.patch.object(main, "get_current_btc_price", return_value=100_000.0),
            mock.patch.object(main.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            try:
                main.run()
            except KeyboardInterrupt:
                pass

        close_v3.assert_not_called()
        process_v3.assert_not_called()

    def test_order_intents_only_v2_for_v2_only_config(self) -> None:
        self._apply_v2_only_env()
        config, main = self._reload()

        now_ts = int(time.time())
        window_start = now_ts - 10
        market = _make_market(window_start=window_start)
        quotes = _no_c_quotes()

        with (
            mock.patch.object(main, "get_current_btc_price", return_value=100_000.0),
            mock.patch.object(main, "find_active_btc_5m_market", return_value=market),
            mock.patch.object(main, "get_strike_price", return_value=99_900.0),
            mock.patch.object(main, "get_best_bid_ask", return_value=quotes),
            mock.patch.object(main, "evaluate", return_value=None),
            mock.patch.object(main, "run_startup_recovery"),
            mock.patch.object(main.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            try:
                main.run()
            except KeyboardInterrupt:
                pass

        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT strategy_version FROM order_intents ORDER BY strategy_version"
            ).fetchall()
            versions = [row["strategy_version"] for row in rows]

        self.assertEqual(config.ENABLE_V2, True)
        self.assertEqual(config.ENABLE_V1, False)
        self.assertEqual(config.ENABLE_V25, False)
        self.assertEqual(config.ENABLE_V3, False)
        self.assertEqual(versions, ["v2"])

        with connect(self.db_path) as conn:
            bad = conn.execute(
                """
                SELECT COUNT(*) AS c FROM order_intents
                WHERE strategy_version IN ('v1', 'v25', 'v3')
                """
            ).fetchone()["c"]
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()
