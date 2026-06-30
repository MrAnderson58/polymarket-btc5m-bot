"""Tests for YES_C shadow virtual trading."""

from __future__ import annotations

import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db, insert_yes_c_shadow_trade
from bot.er_stats import fetch_strategy_counters, YES_C_SHADOW_VERSION
from bot.market_scanner import Btc5mMarket, TokenQuotes
from bot.yes_c_shadow import close_due_yes_c_shadow_trades, process_yes_c_shadow


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
        yes_quotes=TokenQuotes("yes-token", 0.38, 0.39),
        no_quotes=TokenQuotes("no-token", 0.61, 0.62),
    )


def _yes_c_quotes(*, yes_bid: float = 0.38, yes_ask: float = 0.39) -> dict[str, float | None]:
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": 0.61,
        "no_ask": 0.62,
    }


class YesCShadowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "DATABASE_PATH": str(self.db_path),
                "TRADING_MODE": "live",
                "ENABLE_YES_C_SHADOW": "true",
            },
            clear=False,
        )
        self._env_patch.start()
        import bot.config as config
        import bot.yes_c_shadow as yes_c_shadow

        importlib.reload(config)
        importlib.reload(yes_c_shadow)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_shadow_opens_without_execution(self) -> None:
        window_start = _current_window_start(int(time.time()))
        market = _make_market(window_start=window_start)
        now_ts = window_start + 15
        quotes = _yes_c_quotes()

        with (
            mock.patch("bot.yes_c_shadow.time.time", return_value=now_ts),
            mock.patch("bot.execution.attempt_entry_open") as attempt_entry,
        ):
            with connect(self.db_path) as conn:
                process_yes_c_shadow(conn, market, quotes)
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM yes_c_shadow_trades WHERE market_slug = ?",
                    (market.slug,),
                ).fetchone()

        attempt_entry.assert_not_called()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["strategy_name"], "YES_C")
        self.assertAlmostEqual(float(row["entry_price"]), 0.39)

    def test_shadow_funnel_counters_on_entry(self) -> None:
        window_start = _current_window_start(int(time.time()))
        market = _make_market(window_start=window_start)
        now_ts = window_start + 15
        quotes = _yes_c_quotes()

        with mock.patch("bot.yes_c_shadow.time.time", return_value=now_ts):
            with connect(self.db_path) as conn:
                process_yes_c_shadow(conn, market, quotes)
                conn.commit()
                counters = fetch_strategy_counters(conn, YES_C_SHADOW_VERSION)[0]

        self.assertEqual(counters.checks, 1)
        self.assertEqual(counters.window_ok, 1)
        self.assertEqual(counters.price_ok, 1)
        self.assertEqual(counters.already_open_ok, 1)
        self.assertEqual(counters.entry_success, 1)

    def test_shadow_funnel_blocks_outside_window(self) -> None:
        window_start = _current_window_start(int(time.time()))
        market = _make_market(window_start=window_start)
        now_ts = window_start + 45
        quotes = _yes_c_quotes()

        with mock.patch("bot.yes_c_shadow.time.time", return_value=now_ts):
            with connect(self.db_path) as conn:
                process_yes_c_shadow(conn, market, quotes)
                conn.commit()
                counters = fetch_strategy_counters(conn, YES_C_SHADOW_VERSION)[0]

        self.assertEqual(counters.checks, 1)
        self.assertEqual(counters.window_ok, 0)
        self.assertEqual(counters.blocked_by_window, 1)
        self.assertEqual(counters.entry_success, 0)

    def test_shadow_stop_loss_close(self) -> None:
        window_start = _current_window_start(int(time.time()))
        market = _make_market(window_start=window_start)
        entry_ts = window_start + 20
        now_ts = entry_ts + 35

        with connect(self.db_path) as conn:
            insert_yes_c_shadow_trade(
                conn,
                market_slug=market.slug,
                window_start_ts=window_start,
                end_ts=market.end_ts,
                side="YES",
                strategy_name="YES_C",
                entry_price=0.39,
                entry_ts=entry_ts,
            )
            conn.commit()

        quotes = _yes_c_quotes(yes_bid=0.34, yes_ask=0.35)
        with mock.patch("bot.yes_c_shadow.time.time", return_value=now_ts):
            with connect(self.db_path) as conn:
                process_yes_c_shadow(conn, market, quotes)
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM yes_c_shadow_trades WHERE market_slug = ?",
                    (market.slug,),
                ).fetchone()

        assert row is not None
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["exit_reason"], "STOP_LOSS")

    def test_close_due_shadow_trades(self) -> None:
        window_start = _current_window_start(int(time.time())) - 600
        end_ts = window_start + 300
        now_ts = end_ts + 5

        with connect(self.db_path) as conn:
            insert_yes_c_shadow_trade(
                conn,
                market_slug=f"btc-updown-5m-{window_start}",
                window_start_ts=window_start,
                end_ts=end_ts,
                side="YES",
                strategy_name="YES_C",
                entry_price=0.39,
                entry_ts=window_start + 10,
            )
            conn.execute(
                "UPDATE yes_c_shadow_trades SET last_bid = 0.40 WHERE id = 1"
            )
            conn.commit()
            closed = close_due_yes_c_shadow_trades(conn, now_ts)
            conn.commit()
            row = conn.execute("SELECT status FROM yes_c_shadow_trades WHERE id = 1").fetchone()

        self.assertEqual(closed, 1)
        assert row is not None
        self.assertEqual(row["status"], "closed")


class YesCShadowConfigTestCase(unittest.TestCase):
    def test_default_enabled_strategies_is_no_c_only(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            import bot.config as config

            importlib.reload(config)
            self.assertEqual(config.ENABLED_STRATEGIES, frozenset({"NO_C"}))
            self.assertEqual(config.ENABLED_STRATEGIES_V2, frozenset({"NO_C"}))
