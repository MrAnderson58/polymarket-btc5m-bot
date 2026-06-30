"""Tests for NO_C BTC-up filter shadow statistics."""

from __future__ import annotations

import importlib
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.early_reversion_v2 import process_early_reversion_v2
from bot.market_scanner import Btc5mMarket, TokenQuotes
from bot.no_c_filter_shadow import (
    compute_btc_moves,
    format_no_c_filter_shadow_report,
    record_no_c_filter_shadow,
)


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


def _no_c_quotes() -> dict[str, float | None]:
    return {
        "yes_bid": 0.61,
        "yes_ask": 0.63,
        "no_bid": 0.37,
        "no_ask": 0.39,
    }


def _insert_btc_check(
    conn,
    *,
    btc_price: float,
    checked_ts: int,
    market_slug: str = "btc-updown-5m-test",
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


class NoCFilterShadowTestCase(unittest.TestCase):
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
                "ENABLE_NO_C_FILTER_SHADOW": "true",
            },
            clear=False,
        )
        self._env_patch.start()
        import bot.config as config
        import bot.early_reversion_v2 as er_v2
        import bot.execution as execution

        importlib.reload(config)
        importlib.reload(execution)
        importlib.reload(er_v2)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_compute_btc_moves_from_history(self) -> None:
        now_ts = 1_700_000_000
        current_btc = 100_050.0
        with connect(self.db_path) as conn:
            _insert_btc_check(conn, btc_price=100_000.0, checked_ts=now_ts - 30)
            _insert_btc_check(conn, btc_price=100_030.0, checked_ts=now_ts - 60)
            _insert_btc_check(conn, btc_price=100_040.0, checked_ts=now_ts - 90)
            readings = compute_btc_moves(conn, current_btc=current_btc, now_ts=now_ts)

        moves = {item.lookback_sec: item.move_usd for item in readings}
        self.assertAlmostEqual(moves[30], 50.0)
        self.assertAlmostEqual(moves[60], 20.0)
        self.assertAlmostEqual(moves[90], 10.0)

    def test_record_would_block_without_blocking_entry(self) -> None:
        now_ts = 1_700_000_000
        current_btc = 100_050.0
        with connect(self.db_path) as conn:
            _insert_btc_check(conn, btc_price=100_000.0, checked_ts=now_ts - 30)
            _insert_btc_check(conn, btc_price=100_030.0, checked_ts=now_ts - 60)
            _insert_btc_check(conn, btc_price=100_040.0, checked_ts=now_ts - 90)

            with self.assertLogs("bot.no_c_filter_shadow", level="INFO") as logs:
                hits = record_no_c_filter_shadow(
                    conn,
                    market_slug="btc-updown-5m-test",
                    entry_ts=now_ts,
                    entry_price=0.39,
                    current_btc=current_btc,
                )
                conn.commit()
                report = format_no_c_filter_shadow_report(conn)

        self.assertTrue(any("WOULD_BLOCK" in line for line in logs.output))
        self.assertTrue(any(hit.lookback_sec == 30 and hit.threshold_usd == 5.0 for hit in hits))
        self.assertIn("Entries evaluated: 1", report)
        self.assertIn("WOULD_BLOCK", report)

    def test_v2_entry_still_opens_with_filter_shadow(self) -> None:
        window_start = _current_window_start(int(time.time()))
        market = _make_market(window_start=window_start)
        now_ts = window_start + 15
        current_btc = 100_003.0

        with connect(self.db_path) as conn:
            _insert_btc_check(conn, btc_price=100_000.0, checked_ts=now_ts - 30, market_slug=market.slug)
            _insert_btc_check(conn, btc_price=100_030.0, checked_ts=now_ts - 60, market_slug=market.slug)
            _insert_btc_check(conn, btc_price=100_040.0, checked_ts=now_ts - 90, market_slug=market.slug)
            conn.commit()

        with mock.patch("bot.early_reversion_v2.time.time", return_value=now_ts):
            with connect(self.db_path) as conn:
                process_early_reversion_v2(
                    conn,
                    market,
                    _no_c_quotes(),
                    btc_price=current_btc,
                )
                conn.commit()
                trade = conn.execute(
                    "SELECT * FROM early_reversion_v2_trades WHERE market_slug = ?",
                    (market.slug,),
                ).fetchone()
                blocks = conn.execute(
                    """
                    SELECT SUM(would_block_count) AS total
                    FROM no_c_filter_shadow_counters
                    """
                ).fetchone()

        self.assertIsNotNone(trade)
        assert blocks is not None
        self.assertEqual(int(blocks["total"] or 0), 0)

    def test_downward_move_does_not_trigger_would_block(self) -> None:
        now_ts = 1_700_000_000
        current_btc = 99_950.0
        with connect(self.db_path) as conn:
            _insert_btc_check(conn, btc_price=100_000.0, checked_ts=now_ts - 30)
            hits = record_no_c_filter_shadow(
                conn,
                market_slug="btc-updown-5m-test",
                entry_ts=now_ts,
                entry_price=0.39,
                current_btc=current_btc,
            )
            conn.commit()

        self.assertEqual(hits, [])
