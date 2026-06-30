"""Tests for adaptive NO_C BTC-up entry filter."""

from __future__ import annotations

import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.early_reversion_v2 import process_early_reversion_v2
from bot.er_stats import format_er_summary
from bot.market_scanner import Btc5mMarket, TokenQuotes
from bot.no_c_btc_filter import record_no_c_filter_skipped_strict_price, resolve_no_c_filter


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
        yes_quotes=TokenQuotes("yes-token", 0.61, 0.63),
        no_quotes=TokenQuotes("no-token", 0.37, 0.39),
    )


def _insert_btc_check(
    conn,
    *,
    btc_price: float,
    checked_ts: int,
    market_slug: str,
) -> None:
    conn.execute(
        """
        INSERT INTO market_checks (
            market_slug, seconds_remaining, strike_price, btc_price,
            yes_bid, yes_ask, no_bid, no_ask, signal, checked_at
        ) VALUES (?, 200, 100000, ?, 0.6, 0.62, 0.38, 0.4, NULL, datetime(?, 'unixepoch'))
        """,
        (market_slug, btc_price, checked_ts),
    )


class NoCBtcFilterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "DATABASE_PATH": str(self.db_path),
                "TRADING_MODE": "paper",
                "ENABLED_STRATEGIES": "NO_C",
                "ENABLE_NO_C_FILTER_SHADOW": "false",
                "ER_ENTRY_PRICE_OFFSET": "0",
            },
            clear=False,
        )
        self._env_patch.start()
        import bot.config as config
        import bot.early_reversion_v2 as er_v2
        import bot.execution as execution
        import bot.no_c_btc_filter as no_c_btc_filter

        importlib.reload(config)
        importlib.reload(execution)
        importlib.reload(no_c_btc_filter)
        importlib.reload(er_v2)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_normal_mode_when_btc_move_below_threshold(self) -> None:
        now_ts = 1_700_000_000
        with connect(self.db_path) as conn:
            _insert_btc_check(conn, btc_price=100_000.0, checked_ts=now_ts - 30, market_slug="m")
            decision = resolve_no_c_filter(conn, current_btc=100_003.0, now_ts=now_ts)

        self.assertEqual(decision.mode, "NORMAL")
        self.assertAlmostEqual(decision.threshold, 0.40)
        self.assertAlmostEqual(decision.btc_move, 3.0)

    def test_strict_mode_when_btc_move_above_threshold(self) -> None:
        now_ts = 1_700_000_000
        with connect(self.db_path) as conn:
            _insert_btc_check(conn, btc_price=100_000.0, checked_ts=now_ts - 30, market_slug="m")
            decision = resolve_no_c_filter(conn, current_btc=100_009.8, now_ts=now_ts)

        self.assertEqual(decision.mode, "STRICT")
        self.assertAlmostEqual(decision.threshold, 0.36)
        self.assertAlmostEqual(decision.btc_move, 9.8)

    def test_strict_price_skip_blocks_entry(self) -> None:
        window_start = _current_window_start(int(time.time()))
        market = _make_market(window_start=window_start)
        now_ts = window_start + 15
        quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.39,
        }

        with connect(self.db_path) as conn:
            _insert_btc_check(
                conn,
                btc_price=100_000.0,
                checked_ts=now_ts - 30,
                market_slug=market.slug,
            )
            conn.commit()

        with mock.patch("bot.early_reversion_v2.time.time", return_value=now_ts):
            with self.assertLogs("bot.no_c_btc_filter", level="INFO") as logs:
                with connect(self.db_path) as conn:
                    process_early_reversion_v2(
                        conn,
                        market,
                        quotes,
                        btc_price=100_050.0,
                    )
                    conn.commit()
                    trade = conn.execute(
                        "SELECT 1 FROM early_reversion_v2_trades WHERE market_slug = ?",
                        (market.slug,),
                    ).fetchone()

        output = "\n".join(logs.output)
        self.assertIsNone(trade)
        self.assertIn("mode=STRICT", output)
        self.assertIn("SKIPPED", output)
        self.assertIn("reason=STRICT_PRICE", output)
        self.assertIn("required<=0.36", output)

    def test_normal_mode_allows_entry_at_039(self) -> None:
        window_start = _current_window_start(int(time.time()))
        market = _make_market(window_start=window_start)
        now_ts = window_start + 15
        quotes = {
            "yes_bid": 0.61,
            "yes_ask": 0.63,
            "no_bid": 0.37,
            "no_ask": 0.39,
        }

        with connect(self.db_path) as conn:
            _insert_btc_check(
                conn,
                btc_price=100_000.0,
                checked_ts=now_ts - 30,
                market_slug=market.slug,
            )
            conn.commit()

        with mock.patch("bot.early_reversion_v2.time.time", return_value=now_ts):
            with connect(self.db_path) as conn:
                process_early_reversion_v2(
                    conn,
                    market,
                    quotes,
                    btc_price=100_003.0,
                )
                conn.commit()
                trade = conn.execute(
                    "SELECT 1 FROM early_reversion_v2_trades WHERE market_slug = ?",
                    (market.slug,),
                ).fetchone()

        self.assertIsNotNone(trade)

    def test_er_summary_includes_filter_live_block(self) -> None:
        with connect(self.db_path) as conn:
            record_no_c_filter_skipped_strict_price(conn)
            conn.commit()
            summary = format_er_summary(conn)

        self.assertIn("NO_C FILTER LIVE", summary)
        self.assertIn("Skipped by strict price: 1", summary)
        self.assertIn("Normal entries: 0", summary)
        self.assertIn("Strict entries: 0", summary)


if __name__ == "__main__":
    unittest.main()
