"""Phase G.1 — Liquidity & Trend Engine tests."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.adaptive_shock_g1 import compute_adaptive_threshold
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.liquidity_engine_g1 import (
    SIGNAL_SLOW_TREND,
    detect_capitulation,
    detect_slow_trend_shock,
)
from bot.research.market_events.signal_intelligence.liquidity_trend_g1 import run_liquidity_trend_g1
from bot.research.market_events.signal_intelligence.telegram_g1 import render_liquidity_trend_telegram_g1
from bot.research.market_events.signal_intelligence.trend_windows_g1 import (
    analyze_consecutive_pattern,
    analyze_window_trend,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


def _make_red_bars(n: int, *, drop_pct: float = 0.15) -> list[CandleBar]:
    now = int(time.time())
    bars: list[CandleBar] = []
    price = 100.0
    for i in range(n):
        o = price
        c = price * (1 - drop_pct / 100)
        bars.append(CandleBar(
            open_ts=now - (n - i) * 300,
            open=o, high=o, low=c, close=c, volume=1000 + i * 10,
        ))
        price = c
    return bars


class LiquidityTrendG1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {"ME_G1_LIQUIDITY_TREND": "true"}, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v24(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v24", applied)
            self.assertEqual(SCHEMA_VERSION, 38)

    def test_window_trend_15m(self) -> None:
        bars = _make_red_bars(12)
        wt = analyze_window_trend(bars, window_minutes=15)
        self.assertIsNotNone(wt)
        assert wt is not None
        self.assertLess(wt.cumulative_return_pct, 0)
        self.assertGreaterEqual(wt.max_streak, 3)
        self.assertGreater(wt.red_pct, 50)

    def test_consecutive_pattern(self) -> None:
        bars = _make_red_bars(9) + [
            CandleBar(open_ts=1, open=90, high=91, low=89, close=90.5, volume=1000),
        ] + _make_red_bars(7)
        cp = analyze_consecutive_pattern(bars, window_minutes=15)
        self.assertIsNotNone(cp)
        assert cp is not None
        self.assertIn("красных", cp.description)

    def test_slow_trend_shock(self) -> None:
        bars = _make_red_bars(30, drop_pct=0.10)
        slow = detect_slow_trend_shock(bars, window_minutes=120, min_consecutive=8, min_cumulative_pct=1.5)
        self.assertIsNotNone(slow)
        assert slow is not None
        self.assertEqual(slow.direction, "DOWN")

    def test_capitulation_volume(self) -> None:
        bars = _make_red_bars(20, drop_pct=0.4)
        for i in range(-3, 0):
            b = bars[i]
            bars[i] = CandleBar(
                open_ts=b.open_ts, open=b.open, high=b.high, low=b.low,
                close=b.close, volume=b.volume * 5,
            )
        cap = detect_capitulation(bars, min_volume_mult=3.0, min_atr_mult=0.5)
        self.assertIsNotNone(cap)

    def test_adaptive_threshold(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="BTC", n=30, shock=False)
            thr = compute_adaptive_threshold(conn, symbol="BTC")
            self.assertGreater(thr.threshold_pct, 0)
            self.assertIn("BTC", thr.symbol)

    def test_run_g1_pipeline(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="BTC", ret=-2.4)
            seed_candles(conn, symbol="BTC", n=60, shock=True)
            result = run_liquidity_trend_g1(conn, eid)
            self.assertIsNotNone(result)
            row = conn.execute(
                "SELECT signal_type FROM market_events_liquidity_trend_g1 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)

    def test_telegram_human_readable(self) -> None:
        text = render_liquidity_trend_telegram_g1(
            symbol="BTC",
            signal_type=SIGNAL_SLOW_TREND,
            headline="Похоже на капитуляцию",
            window_label="15m",
            streak_line="9 красных свечей подряд",
            move_pct=-2.1,
            funding_line="↓",
            oi_line="↑",
            volume_line="3.8×",
            historical_rate=0.78,
            reversal_probability=0.81,
        )
        self.assertIn("BTCUSDT", text)
        self.assertIn("капитуляцию", text)
        self.assertIn("81%", text)
        self.assertIn("Ждать R2", text)
