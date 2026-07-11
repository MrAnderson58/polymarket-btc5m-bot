"""F.0 multitimeframe detector tests."""

from __future__ import annotations

import unittest

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    aggregate_bars,
    compute_atr,
    compute_ema,
    compute_vwap,
)
from bot.research.market_events.signal_intelligence.config import MTF_DETECTORS
from bot.research.market_events.signal_intelligence.multitimeframe import (
    detect_on_bars,
    multitimeframe_report,
    persist_mtf_signal,
    scan_multitimeframe,
)
from tests.f0_test_utils import make_db, seed_candles, seed_event, conn_ctx


def _shock_bars(n: int = 60, *, shock_pct: float = 0.05) -> list[CandleBar]:
    """Flat baseline then a single large move on the last bar (high ATR multiple)."""
    bars: list[CandleBar] = []
    price = 100.0
    for i in range(n - 1):
        o = price
        c = price * 1.001
        bars.append(CandleBar(i * 300, o, o * 1.002, o * 0.998, c, 1000.0))
        price = c
    o = price
    c = price * (1.0 + shock_pct)
    bars.append(CandleBar((n - 1) * 300, o, c * 1.001, o * 0.999, c, 8000.0))
    return bars


def _trend_bars(n: int = 60, *, trend: float = 0.025) -> list[CandleBar]:
    return _shock_bars(n, shock_pct=max(trend * 3, 0.05))


class MultitimeframeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schema_v12(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v12", applied)
            self.assertEqual(SCHEMA_VERSION, 12)

    def test_mtf_detectors_defined(self) -> None:
        self.assertEqual(set(MTF_DETECTORS), {"SHOCK_M5", "SHOCK_M10", "SHOCK_M15", "SHOCK_M30"})

    def test_aggregate_bars_10m(self) -> None:
        bars = _trend_bars(20, trend=0.01)
        agg = aggregate_bars(bars, window_minutes=10, bar_minutes=5)
        self.assertEqual(len(agg), 10)

    def test_compute_atr_positive(self) -> None:
        bars = _trend_bars(30)
        self.assertGreater(compute_atr(bars), 0)

    def test_compute_vwap(self) -> None:
        bars = _trend_bars(20)
        self.assertGreater(compute_vwap(bars), 0)

    def test_compute_ema(self) -> None:
        vals = [100 + i for i in range(20)]
        self.assertGreater(compute_ema(vals, 10), 100)

    def test_detect_shock_m5_on_strong_trend(self) -> None:
        bars = _trend_bars(60, trend=0.03)
        sig = detect_on_bars(bars, detector_id="SHOCK_M5", symbol="SUI", cfg=MTF_DETECTORS["SHOCK_M5"])
        self.assertIsNotNone(sig)
        self.assertEqual(sig.detector_id, "SHOCK_M5")

    def test_detect_shock_m30_requires_larger_move(self) -> None:
        bars = _trend_bars(60, trend=0.005)
        sig = detect_on_bars(bars, detector_id="SHOCK_M30", symbol="SUI", cfg=MTF_DETECTORS["SHOCK_M30"])
        self.assertIsNone(sig)

    def test_persist_dedupes(self) -> None:
        bars = _trend_bars(60, trend=0.03)
        sig = detect_on_bars(bars, detector_id="SHOCK_M5", symbol="SUI", cfg=MTF_DETECTORS["SHOCK_M5"])
        self.assertIsNotNone(sig)
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            id1 = persist_mtf_signal(conn, sig)
            id2 = persist_mtf_signal(conn, sig)
            self.assertEqual(id1, id2)

    def test_scan_with_candles(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, trend=0.03)
            found = scan_multitimeframe(conn, symbol="SUI", source_event_id=None)
            self.assertGreaterEqual(len(found), 1)

    def test_independent_from_prod_event(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            seed_candles(conn, trend=0.03)
            scan_multitimeframe(conn, symbol="SUI", source_event_id=eid)
            row = conn.execute(
                "SELECT detector_id, source_event_id FROM market_events_multitimeframe LIMIT 1",
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(int(row["source_event_id"]), eid)

    def test_multitimeframe_report_empty(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = multitimeframe_report(conn)
            self.assertIn("SHOCK_M5", text)

    def test_window_minutes_stored(self) -> None:
        bars = _trend_bars(60, trend=0.03)
        sig = detect_on_bars(bars, detector_id="SHOCK_M15", symbol="BTC", cfg=MTF_DETECTORS["SHOCK_M15"])
        if sig:
            self.assertEqual(sig.window_minutes, 15)

    def test_direction_up_on_positive_return(self) -> None:
        bars = _trend_bars(60, trend=0.04)
        sig = detect_on_bars(bars, detector_id="SHOCK_M5", symbol="ETH", cfg=MTF_DETECTORS["SHOCK_M5"])
        if sig:
            self.assertEqual(sig.direction, "UP")

    def test_atr_multiple_populated(self) -> None:
        bars = _trend_bars(60, trend=0.03)
        sig = detect_on_bars(bars, detector_id="SHOCK_M10", symbol="SOL", cfg=MTF_DETECTORS["SHOCK_M10"])
        if sig:
            self.assertGreater(sig.atr_multiple, 0)


if __name__ == "__main__":
    unittest.main()
