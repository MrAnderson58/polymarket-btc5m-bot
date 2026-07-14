"""Phase F.4 — visual intelligence and trend shock v2 tests."""

from __future__ import annotations

import json
import time
import unittest

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.signal_report_f2 import run_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_f4 import render_final_telegram_f4
from bot.research.market_events.signal_intelligence.trend_shock import detect_trend_shock
from bot.research.market_events.signal_intelligence.trend_shock_v2 import (
    STAGE_CAPITULATION,
    STAGE_SHOCK,
    detect_trend_shock_v2,
    scan_trend_shock_v2,
)
from bot.research.market_events.signal_intelligence.visual_chart_analyze_f4 import analyze_chart_text
from bot.research.market_events.signal_intelligence.visual_intel_f4 import run_visual_intel
from bot.research.market_events.signal_intelligence.visual_platform_f4 import detect_platform
from bot.research.market_events.signal_intelligence.visual_text_extract_f4 import extract_chart_text
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event
from tests.test_trend_shock_validation import _cascade_bars_iran_scenario, _seed_bars


class VisualTrendF4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schema_v18(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v18", applied)
            self.assertEqual(SCHEMA_VERSION, 37)

    def test_platform_detect_tradingview(self) -> None:
        self.assertEqual(
            detect_platform("BTCUSDT 1h chart on TradingView — demand zone"),
            "TradingView",
        )

    def test_extract_entry_tp_sl(self) -> None:
        text = "SOLUSDT 15m LONG\nEntry: 142.5\nTP: 148\nSL: 139.8\nDemand zone sweep"
        ext = extract_chart_text(text)
        self.assertEqual(ext.ticker, "SOL")
        self.assertEqual(ext.timeframe, "15m")
        self.assertEqual(ext.direction, "UP")
        self.assertAlmostEqual(ext.entry or 0, 142.5)
        self.assertAlmostEqual(ext.tp or 0, 148.0)

    def test_chart_structure_from_caption(self) -> None:
        chart = analyze_chart_text(
            "LL formed, liquidity sweep into demand zone. Expect long after BOS."
        )
        self.assertIn("demand", chart.demand_zones)
        self.assertIn("liquidity_sweep", chart.liquidity_levels)
        self.assertIn("LL", chart.structure_labels)

    def test_trend_v2_cascade_60m(self) -> None:
        bars = _cascade_bars_iran_scenario()
        for _ in range(12):
            bars.extend([
                CandleBar(
                    open_ts=bars[-1].open_ts + 300,
                    open=bars[-1].close,
                    high=bars[-1].close * 1.001,
                    low=bars[-1].close * 0.992,
                    close=bars[-1].close * 0.992,
                    volume=3500.0,
                )
            ])
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            sig = detect_trend_shock_v2(conn, bars, symbol="BTC", event_id=None)
            self.assertIsNotNone(sig)
            assert sig is not None
            self.assertIn(sig.primary.stage, (STAGE_SHOCK, STAGE_CAPITULATION, STAGE_SHOCK))
            self.assertGreaterEqual(sig.primary.window_minutes, 10)
            v1 = detect_trend_shock(bars, symbol="BTC")
            self.assertIsNotNone(v1)

    def test_visual_intel_persist_and_crosscheck(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="BTC", n=60, shock=True)
            eid = seed_event(conn, symbol="BTC", ret=-5.8)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_event_context (
                  event_id, context_type, source, source_record_id,
                  context_ts, time_delta_seconds, relevance_score, context_json, created_at
                ) VALUES (?, 'TELEGRAM_SIGNAL', 'test_channel', '1', ?, 0, 0.8, ?, ?)
                """,
                (
                    eid, now,
                    json.dumps({
                        "has_photo": True,
                        "raw_text": (
                            "BTCUSDT 30m TradingView — LL/LH structure. "
                            "Demand zone liquidity sweep. Entry 95000 TP 92000 SL 96500 SHORT"
                        ),
                        "content_type": "TECHNICAL_LEVELS",
                    }),
                    now,
                ),
            )
            run_signal_report_f2(conn, eid)
            result = run_visual_intel(conn, eid)
            self.assertIsNotNone(result)
            row = conn.execute(
                "SELECT platform FROM market_events_visual_intel_f4 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertEqual(row["platform"], "TradingView")

    def test_final_telegram_f4_layout(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            bars = _cascade_bars_iran_scenario()
            _seed_bars(conn, bars, symbol="BTC")
            eid = seed_event(conn, symbol="BTC", ret=-5.8)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_event_context (
                  event_id, context_type, source, source_record_id,
                  context_ts, time_delta_seconds, relevance_score, context_json, created_at
                ) VALUES (?, 'TELEGRAM_SIGNAL', 'trader', '2', ?, 0, 0.7, ?, ?)
                """,
                (
                    eid, now,
                    json.dumps({
                        "has_photo": True,
                        "raw_text": "BTC demand zone — ожидает снятие ликвидности в зоне спроса",
                    }),
                    now,
                ),
            )
            run_signal_report_f2(conn, eid)
            scan_trend_shock_v2(conn, symbol="BTC", source_event_id=eid)
            run_visual_intel(conn, eid)
            text = render_final_telegram_f4(conn, eid)
            self.assertIn("🚨 BTCUSDT", text)
            self.assertIn("Trend Shock", text)
            self.assertIn("Уверенность", text)
            self.assertIn("Вероятность продолжения", text)
            self.assertIn("Причина:", text)
            self.assertIn("План:", text)
            self.assertIn("PAPER ONLY", text)
            self.assertIn("%", text)


if __name__ == "__main__":
    unittest.main()
