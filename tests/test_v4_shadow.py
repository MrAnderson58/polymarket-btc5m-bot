"""Tests for V4 Shadow virtual strategy."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.market_scanner import Btc5mMarket, TokenQuotes
from bot.v4.observe import ObservationSnapshot, build_observation, in_observe_phase
from bot.v4.pullback import pullback_reached, update_peak_ask
from bot.v4.score import compute_trend_score, meets_entry_threshold
from bot.v4.shadow_trade import _process_v4_trailing_tick, process_v4_shadow
from bot.v4.trend_detector import TrendSignal, detect_trend


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


def _obs(
    *,
    t: int,
    window_start: int,
    delta: float,
    yes_ask: float,
    no_ask: float,
) -> ObservationSnapshot:
    return ObservationSnapshot(
        timestamp=window_start + t,
        window_start=window_start,
        seconds_from_start=t,
        seconds_left=300 - t,
        btc_price=100_000 + delta,
        strike=100_000.0,
        delta=delta,
        yes_bid=yes_ask - 0.01,
        yes_ask=yes_ask,
        no_bid=no_ask - 0.01,
        no_ask=no_ask,
        trend_score=None,
        trend_side=None,
        spread=0.01,
    )


class V4ShadowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_observe_phase_duration(self) -> None:
        self.assertTrue(in_observe_phase(60, 120))
        self.assertFalse(in_observe_phase(120, 120))

    def test_detect_yes_trend(self) -> None:
        window_start = 1_700_000_000
        history = [
            _obs(t=10 + i, window_start=window_start, delta=10 + i * 3, yes_ask=0.40 + i * 0.01, no_ask=0.60 - i * 0.01)
            for i in range(12)
        ]
        signal = detect_trend(history)
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, "YES")

    def test_score_threshold(self) -> None:
        signal = TrendSignal(side="YES", delta_change=60, ask_change=0.04, consistency=0.85)
        score, prob = compute_trend_score(signal)
        self.assertGreaterEqual(score, 8)
        self.assertGreaterEqual(prob, 0.70)
        self.assertTrue(meets_entry_threshold(score, prob, min_score=8, min_probability=0.70))

    def test_pullback_detection(self) -> None:
        peak = update_peak_ask(None, 0.50)
        peak = update_peak_ask(peak, 0.52)
        self.assertFalse(pullback_reached(peak, 0.51, 0.02))
        self.assertTrue(pullback_reached(peak, 0.50, 0.02))

    def test_trailing_activates_at_plus_three_cents(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE t (
                max_price_seen REAL, trailing_active INTEGER,
                highest_price REAL, trailing_activation_price REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO t VALUES (0.38, 0, NULL, NULL)"
        )
        trade = conn.execute("SELECT * FROM t").fetchone()
        tick = _process_v4_trailing_tick(trade, 0.38, 0.41)
        self.assertTrue(tick.snapshot.trailing_active)
        self.assertAlmostEqual(tick.snapshot.trailing_stop_price, 0.40)

    def test_process_v4_shadow_records_observations(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "DATABASE_PATH": str(self.db_path),
                "ENABLE_V4_SHADOW": "true",
                "V4_OBSERVE_SECONDS": "120",
            },
            clear=False,
        ):
            import bot.config as config
            import bot.v4.shadow_trade as shadow_trade

            importlib.reload(config)
            importlib.reload(shadow_trade)

        now_ts = int(time.time())
        window_start = (now_ts // 300) * 300
        market = _make_market(window_start=window_start)
        quotes = {
            "yes_bid": 0.55,
            "yes_ask": 0.57,
            "no_bid": 0.38,
            "no_ask": 0.39,
        }

        with connect(self.db_path) as conn:
            process_v4_shadow(
                conn,
                market,
                quotes,
                btc_price=100_000.0,
                strike=99_950.0,
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM v4_shadow_observations"
            ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_v4_does_not_use_execution(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DATABASE_PATH": str(self.db_path), "ENABLE_V4_SHADOW": "true"},
            clear=False,
        ):
            import bot.execution as execution

            with mock.patch.object(execution, "attempt_entry_open") as entry_mock:
                import bot.v4.shadow_trade as shadow_trade

                importlib.reload(shadow_trade)
                now_ts = int(time.time())
                window_start = (now_ts // 300) * 300
                market = _make_market(window_start=window_start)
                quotes = {
                    "yes_bid": 0.55,
                    "yes_ask": 0.57,
                    "no_bid": 0.38,
                    "no_ask": 0.39,
                }
                with connect(self.db_path) as conn:
                    process_v4_shadow(
                        conn,
                        market,
                        quotes,
                        btc_price=100_000.0,
                        strike=99_950.0,
                    )
            entry_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
