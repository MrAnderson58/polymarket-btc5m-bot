"""Phase F.3 Trend Shock — detector, ranking, premium Telegram, validation tests."""

from __future__ import annotations

import time
import unittest

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.entry_stages_f3 import (
    STAGE_WATCH,
    compute_entry_stage,
    run_entry_stage,
)
from bot.research.market_events.signal_intelligence.multitimeframe import detect_on_bars
from bot.research.market_events.signal_intelligence.config import MTF_DETECTORS
from bot.research.market_events.signal_intelligence.signal_ranking_f3 import (
    compute_rank_score,
    should_send_ranked_alert,
)
from bot.research.market_events.signal_intelligence.signal_report_f2 import run_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_trend_premium import render_trend_premium_v2
from bot.research.market_events.signal_intelligence.trend_shock import (
    TREND_SHOCK_DOWN,
    TREND_SHOCK_UP,
    detect_trend_shock,
    scan_trend_shock,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


def _cascade_bars_iran_scenario() -> list[CandleBar]:
    """Gradual cascade: many small red candles then acceleration — last bar modest."""
    now = int(time.time())
    bars: list[CandleBar] = []
    price = 100.0
    for i in range(36):
        ts = now - (36 - i) * 300
        if i < 24:
            drop = 0.003 + (i * 0.0002)
        elif i < 30:
            drop = 0.008
        else:
            drop = 0.004
        o = price
        c = price * (1 - drop)
        h = o * 1.001
        l = c * 0.999
        vol = 2000.0 if i >= 24 else 800.0 + i * 20
        bars.append(CandleBar(open_ts=ts, open=o, high=h, low=l, close=c, volume=vol))
        price = c
    return bars


def _seed_bars(conn, bars: list[CandleBar], *, symbol: str = "BTC") -> None:
    now = int(time.time())
    for b in bars:
        conn.execute(
            """
            INSERT OR IGNORE INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
            ) VALUES ('binance_futures', ?, '5m', ?, ?, ?, ?, ?, ?, 'test', ?)
            """,
            (symbol, b.open_ts, b.open, b.high, b.low, b.close, b.volume, now),
        )


class TrendShockValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schema_v16(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v18", applied)
            self.assertEqual(SCHEMA_VERSION, 28)

    def test_cascade_detected_by_trend_not_single_bar_mtf(self) -> None:
        bars = _cascade_bars_iran_scenario()
        trend = detect_trend_shock(bars, symbol="BTC")
        self.assertIsNotNone(trend)
        assert trend is not None
        self.assertEqual(trend.primary.trend_class, TREND_SHOCK_DOWN)
        self.assertGreaterEqual(trend.primary.window_minutes, 10)
        self.assertLess(abs(bars[-1].close / bars[-1].open - 1) * 100, 1.0)

        mtf_hits = 0
        for det_id, cfg in MTF_DETECTORS.items():
            if detect_on_bars(bars, detector_id=det_id, symbol="BTC", cfg=cfg):
                mtf_hits += 1
        self.assertLess(mtf_hits, len(MTF_DETECTORS))

    def test_trend_shock_persisted_on_scan(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            bars = _cascade_bars_iran_scenario()
            _seed_bars(conn, bars, symbol="BTC")
            eid = seed_event(conn, symbol="BTC", ret=-3.2)
            sig = scan_trend_shock(conn, symbol="BTC", source_event_id=eid)
            self.assertIsNotNone(sig)
            row = conn.execute(
                "SELECT trend_class FROM market_events_trend_shock WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn(row["trend_class"], (TREND_SHOCK_UP, TREND_SHOCK_DOWN))

    def test_entry_stage_watch_on_trend(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            bars = _cascade_bars_iran_scenario()
            _seed_bars(conn, bars, symbol="ETH")
            eid = seed_event(conn, symbol="ETH", ret=-5.0)
            scan_trend_shock(conn, symbol="ETH", source_event_id=eid)
            stage = compute_entry_stage(conn, event_id=eid)
            self.assertEqual(stage.stage, STAGE_WATCH)

    def test_premium_telegram_compact(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            bars = _cascade_bars_iran_scenario()
            _seed_bars(conn, bars, symbol="SOL")
            eid = seed_event(conn, symbol="SOL", ret=-4.3)
            scan_trend_shock(conn, symbol="SOL", source_event_id=eid)
            run_signal_report_f2(conn, eid)
            run_entry_stage(conn, eid)
            text = render_trend_premium_v2(conn, eid)
            self.assertIn("SOLUSDT", text)
            self.assertIn("Откат", text)
            self.assertIn("%", text)
            self.assertNotIn("0.", text.split("Откат")[1][:8])
            self.assertIn("TP", text)
            self.assertIn("SL", text)
            self.assertIn("ИИ:", text)
            self.assertIn("PAPER ONLY", text)
            self.assertLess(len(text), 600)

    def test_signal_ranking_blocks_weak(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, symbol="DOGE", ret=-0.5)
            score = compute_rank_score(conn, eid)
            self.assertLess(score, 45.0)
            self.assertFalse(should_send_ranked_alert(conn, eid))

    def test_up_trend_shock(self) -> None:
        bars = _cascade_bars_iran_scenario()
        flipped = [
            CandleBar(
                open_ts=b.open_ts,
                open=200 - (b.open - 100),
                high=200 - (b.low - 100),
                low=200 - (b.high - 100),
                close=200 - (b.close - 100),
                volume=b.volume,
            )
            for b in bars
        ]
        trend = detect_trend_shock(flipped, symbol="SOL")
        self.assertIsNotNone(trend)
        assert trend is not None
        self.assertEqual(trend.primary.trend_class, TREND_SHOCK_UP)


if __name__ == "__main__":
    unittest.main()
