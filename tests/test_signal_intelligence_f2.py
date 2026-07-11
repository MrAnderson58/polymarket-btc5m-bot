"""Phase F.2 professional trading intelligence tests."""

from __future__ import annotations

import json
import time
import unittest

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.market_event_alerts import format_shock_alert
from bot.research.market_events.signal_intelligence.ai_analyst_v2 import analyze_ai_v2, build_ai_input_json
from bot.research.market_events.signal_intelligence.atr_expansion_f2 import analyze_atr_expansion
from bot.research.market_events.signal_intelligence.exchange_consensus_f2 import (
    CONFIDENCE_ADJ,
    CONSENSUS_PARTIAL,
    CONSENSUS_STRONG,
    CONSENSUS_WEAK,
    compute_exchange_consensus,
)
from bot.research.market_events.signal_intelligence.market_structure_f2 import analyze_market_structure
from bot.research.market_events.signal_intelligence.signal_report_f1 import run_signal_report_f1
from bot.research.market_events.signal_intelligence.signal_report_f2 import (
    build_signal_report_f2,
    persist_signal_report_f2,
    run_signal_report_f2,
)
from bot.research.market_events.signal_intelligence.telegram_f2 import format_shock_f2
from bot.research.market_events.signal_intelligence.volume_intel_f2 import analyze_volume_intel
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


class SignalIntelligenceF2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schema_v14(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v14", applied)
            self.assertEqual(SCHEMA_VERSION, 14)

    def test_exchange_consensus_from_candles(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SUI", n=30, shock=True)
            result = compute_exchange_consensus(
                conn, symbol="SUI", shock_return_pct=-8.0, window_seconds=900,
            )
            self.assertIn(result.consensus, {CONSENSUS_STRONG, CONSENSUS_PARTIAL, CONSENSUS_WEAK})
            self.assertEqual(len(result.venues), 3)

    def test_consensus_confidence_adjustment(self) -> None:
        self.assertGreater(CONFIDENCE_ADJ[CONSENSUS_STRONG], 0)
        self.assertLess(CONFIDENCE_ADJ[CONSENSUS_WEAK], 0)

    def test_volume_intel(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="OIL", n=60, shock=True)
            vol = analyze_volume_intel(conn, symbol="OIL")
            self.assertGreater(vol.rvol_20, 0)
            self.assertIn("×", vol.volume_label)

    def test_market_structure(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SUI", n=40, shock=True)
            ms = analyze_market_structure(conn, symbol="SUI", shock_return_pct=-6.0)
            self.assertIsInstance(ms.labels, list)
            self.assertGreater(len(ms.labels), 0)

    def test_atr_expansion(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SUI", n=80, shock=True)
            atr = analyze_atr_expansion(conn, symbol="SUI")
            self.assertGreaterEqual(atr.atr_percentile, 0)
            self.assertLessEqual(atr.atr_percentile, 100)

    def test_ai_analyst_v2(self) -> None:
        payload = build_ai_input_json(
            symbol="OIL",
            shock_return_pct=4.2,
            exchange_consensus=CONSENSUS_WEAK,
            funding_regime="accelerating",
            oi_regime="rising",
            volume_label="4.8× среднего",
            atr_percentile=97.0,
            structure_labels=["Liquidity Sweep"],
            correlation_verdict="CORRELATION_BROKEN",
            correlation_peers=[],
            historical_count=74,
            historical_reversal_rate=0.82,
            news_count=0,
        )
        ai = analyze_ai_v2(payload)
        self.assertIn("ликвидности", ai.summary_ru.lower())

    def test_build_and_persist_f2_report(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="OIL", n=60, shock=True)
            eid = seed_event(conn, symbol="OIL", ret=4.2)
            run_signal_report_f1(conn, eid)
            report = build_signal_report_f2(conn, eid)
            self.assertIsNotNone(report)
            assert report is not None
            self.assertIn(report.exchange_consensus, {CONSENSUS_STRONG, CONSENSUS_PARTIAL, CONSENSUS_WEAK})
            self.assertGreaterEqual(report.confidence_score, 0)
            self.assertLessEqual(report.confidence_score, 10)
            persist_signal_report_f2(conn, report)
            row = conn.execute(
                "SELECT * FROM market_events_signal_reports_f2 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("Consensus", row["telegram_rendered"])

    def test_funding_oi_history_table(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SUI")
            eid = seed_event(conn)
            run_signal_report_f1(conn, eid)
            run_signal_report_f2(conn, eid)
            n = conn.execute(
                "SELECT COUNT(*) FROM market_events_funding_oi_history_f2 WHERE event_id = ?",
                (eid,),
            ).fetchone()[0]
            self.assertGreaterEqual(int(n), 1)

    def test_premium_telegram_layout(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="OIL", n=60, shock=True)
            eid = seed_event(conn, symbol="OIL", ret=4.2)
            run_signal_report_f2(conn, eid)
            text = format_shock_f2(conn, eid)
            self.assertIn("🚨 OIL", text)
            self.assertIn("Уверенность", text)
            self.assertIn("Consensus", text)
            self.assertIn("━━━━━━━━━━━━", text)
            self.assertIn("PAPER ONLY", text)

    def test_format_shock_alert_uses_f2(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SUI")
            eid = seed_event(conn, ret=-7.0)
            run_signal_report_f2(conn, eid)
            text = format_shock_alert(conn, eid)
            self.assertIn("Consensus", text)

    def test_historical_similarity_v2_seeded(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SUI", n=40)
            now = int(time.time())
            for i, ret in enumerate([-8.0, -7.8, -8.2, -7.5]):
                conn.execute(
                    """
                    INSERT INTO market_events (
                      event_ts, detected_ts, venue, symbol, direction, phase,
                      trigger_window_seconds, return_pct, volume_zscore, classification,
                      detector_version, detector_triggers_json, dedup_key, created_at
                    ) VALUES (?, ?, 'binance_futures', 'SUI', 'DOWN', 'SHOCK_DETECTED',
                      900, ?, 2.5, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
                    """,
                    (now - 3600 * (i + 1), now, ret, f"hist-f2-{i}", now),
                )
            eid = seed_event(conn, ret=-8.1)
            run_signal_report_f1(conn, eid)
            report = build_signal_report_f2(conn, eid)
            self.assertIsNotNone(report)
            assert report is not None
            self.assertGreater(report.historical_count, 0)


if __name__ == "__main__":
    unittest.main()
