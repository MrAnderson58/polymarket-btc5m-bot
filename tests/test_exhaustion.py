"""F.0 trend exhaustion detector tests."""

from __future__ import annotations

import json
import unittest

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.exhaustion import (
    detect_exhaustion,
    exhaustion_report,
    persist_exhaustion,
    scan_exhaustion,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


def _green_streak_bars(streak: int = 5, *, vol_scale: float = 3.0) -> list[CandleBar]:
    bars: list[CandleBar] = []
    price = 100.0
    for i in range(40):
        up = i >= 40 - streak
        o = price
        if up:
            price *= 1.025
        else:
            price *= 1.001
        c = price
        vol = 500.0 if not up else 500.0 * vol_scale
        bars.append(CandleBar(i * 300, o, c * 1.005, o * 0.995, c, vol))
    return bars


class ExhaustionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_detect_exhaustion_on_green_streak(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(5), symbol="SUI")
        self.assertIsNotNone(sig)
        self.assertGreaterEqual(sig.exhaustion_score, 55)

    def test_no_exhaustion_on_flat(self) -> None:
        bars = [CandleBar(i * 300, 100, 100.5, 99.5, 100, 500) for i in range(40)]
        self.assertIsNone(detect_exhaustion(bars, symbol="SUI"))

    def test_reasons_json_populated(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(6), symbol="ETH")
        self.assertIsNotNone(sig)
        self.assertTrue(any("candles" in r for r in sig.reasons))

    def test_persist_exhaustion(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(5), symbol="SOL")
        self.assertIsNotNone(sig)
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = persist_exhaustion(conn, sig, source_event_id=1)
            self.assertIsNotNone(eid)

    def test_scan_exhaustion_with_candles(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, trend=0.025)
            sig = scan_exhaustion(conn, symbol="SUI")
            self.assertIsNotNone(sig)

    def test_exhaustion_no_paper_trade(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_event(conn)
            seed_candles(conn, trend=0.03)
            scan_exhaustion(conn, symbol="SUI", source_event_id=1)
            n = conn.execute("SELECT COUNT(*) FROM paper_strategy_runs").fetchone()[0]
            self.assertEqual(int(n), 0)

    def test_exhaustion_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = exhaustion_report(conn)
            self.assertIn("EXHAUSTION", text)

    def test_event_type_trend_exhaustion(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(5), symbol="BTC")
        self.assertIsNotNone(sig)
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            persist_exhaustion(conn, sig)
            row = conn.execute("SELECT event_type FROM market_events_exhaustion LIMIT 1").fetchone()
            self.assertEqual(row["event_type"], "TREND_EXHAUSTION")

    def test_vwap_distance_computed(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(5), symbol="XRP")
        self.assertIsNotNone(sig)
        self.assertIsNotNone(sig.vwap_distance_pct)

    def test_ema_distances_computed(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(5), symbol="LINK")
        self.assertIsNotNone(sig)
        self.assertIsNotNone(sig.ema20_distance_pct)

    def test_volume_expansion_in_reasons(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(5, vol_scale=4.0), symbol="DOGE")
        self.assertIsNotNone(sig)
        self.assertTrue(any("volume" in r for r in sig.reasons))

    def test_dedup_prevents_duplicate(self) -> None:
        sig = detect_exhaustion(_green_streak_bars(5), symbol="ADA")
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            persist_exhaustion(conn, sig)
            again = persist_exhaustion(conn, sig)
            self.assertIsNone(again)


if __name__ == "__main__":
    unittest.main()
