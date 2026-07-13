"""G3.3 weighted trend coverage tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    STATE_INSUFFICIENT,
    STATE_PROVISIONAL,
    build_candidates_g31,
    evaluate_symbol_candidate_g31,
    persist_candidates_g31,
)
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.recorder_g3 import (
    SnapshotPayloadG3,
    persist_snapshot_g3,
)
from bot.research.market_events.signal_intelligence.telegram_g3 import format_professional_telegram_g3
from bot.research.market_events.signal_intelligence.trend_coverage_g33 import (
    BARS_FOR_FULL_COVERAGE,
    WINDOW_WEIGHTS,
    compute_trend_coverage,
    format_candidate_coverage_report,
    format_provisional_reason,
    trend_coverage_dashboard,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3


def _make_bars(n: int, *, start_price: float = 100.0) -> list[CandleBar]:
    now = int(time.time())
    bars: list[CandleBar] = []
    price = start_price
    for i in range(n):
        o, c = price, price * 0.998
        price = c
        bars.append(CandleBar(
            open_ts=now - (n - i) * 300,
            open=o,
            high=max(o, c) * 1.001,
            low=min(o, c) * 0.999,
            close=c,
            volume=1000.0,
        ))
    return bars


class TrendCoverageG33Tests(unittest.TestCase):
    def test_window_weights_sum_to_100(self) -> None:
        self.assertAlmostEqual(sum(WINDOW_WEIGHTS.values()), 100.0)

    def test_partial_windows_coverage_pct(self) -> None:
        bars = _make_bars(6)
        cov = compute_trend_coverage(bars, [], symbol="SOL")
        self.assertAlmostEqual(cov.coverage_pct, 55.0)
        available = [w for w in cov.windows if w.available]
        self.assertEqual({w.window_minutes for w in available}, {5, 15, 30})

    def test_weight_redistribution_for_score(self) -> None:
        bars = _make_bars(6)
        trends = [
            TrendWindowG3(
                symbol="SOL", window_minutes=5, pattern_type="slow_bleed",
                consecutive_candles=3, trend_score=60.0, direction="DOWN",
                details={},
            ),
            TrendWindowG3(
                symbol="SOL", window_minutes=15, pattern_type="slow_bleed",
                consecutive_candles=5, trend_score=40.0, direction="DOWN",
                details={},
            ),
        ]
        cov = compute_trend_coverage(bars, trends, symbol="SOL")
        expected = round((60 * 15 + 40 * 20 + cov.windows[2].trend_score * 20) / 55.0, 1)
        self.assertEqual(cov.weighted_trend_score, expected)

    def test_full_24h_coverage_is_100(self) -> None:
        bars = _make_bars(BARS_FOR_FULL_COVERAGE)
        cov = compute_trend_coverage(bars, [], symbol="BTC")
        self.assertEqual(cov.coverage_pct, 100.0)
        self.assertFalse(cov.provisional)

    def test_provisional_reason_format(self) -> None:
        reason = format_provisional_reason(42.0)
        self.assertIn("Confidence provisional", reason)
        self.assertIn("42%", reason)

    def test_telegram_coverage_warning(self) -> None:
        msg = format_professional_telegram_g3(
            symbol="SOL",
            direction="SHORT",
            confidence=7.5,
            probability=0.8,
            market_score=70,
            liquidity_state="Capitulation",
            trend_summary="12 red candles",
            funding=-0.0001,
            oi_rising=True,
            btc_context="Neutral",
            reasons=["Liquidations"],
            trade_plan={"entry": 1, "sl": 2, "tp1": 3, "tp2": 4, "tp3": 5, "risk_reward": 2.5, "position_size_pct": 1},
            claude_summary=None,
            historical=[],
            signal_uuid="test-uuid",
            trend_coverage_pct=68.0,
        )
        self.assertIn("Coverage", msg)
        self.assertIn("68%", msg)
        self.assertIn("⚠ История ещё накапливается.", msg)

    def test_telegram_no_warning_at_full_coverage(self) -> None:
        msg = format_professional_telegram_g3(
            symbol="SOL",
            direction="SHORT",
            confidence=7.5,
            probability=0.8,
            market_score=70,
            liquidity_state="Capitulation",
            trend_summary="12 red candles",
            funding=-0.0001,
            oi_rising=True,
            btc_context="Neutral",
            reasons=["Liquidations"],
            trade_plan={"entry": 1, "sl": 2, "tp1": 3, "tp2": 4, "tp3": 5, "risk_reward": 2.5, "position_size_pct": 1},
            claude_summary=None,
            historical=[],
            signal_uuid="test-uuid",
            trend_coverage_pct=100.0,
        )
        self.assertNotIn("⚠ История ещё накапливается.", msg)


class CandidateG33IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g33.db"
        self._env = patch.dict(os.environ, {"ME_G31_CANDIDATE_PIPELINE": "true"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v32_columns(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v33", applied)
            self.assertEqual(SCHEMA_VERSION, 33)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(market_candidate_g31)")}
            self.assertIn("trend_coverage_pct", cols)
            self.assertIn("trend_windows_json", cols)

    def test_short_history_is_provisional_not_insufficient(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            for i in range(6):
                ts = now - (6 - i) * 300
                conn.execute(
                    """
                    INSERT INTO market_events_historical_candles (
                      venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
                    ) VALUES ('binance_futures', 'SOL', '5m', ?, 100, 101, 99, 99.5, 1000, 'test', ?)
                    """,
                    (ts, now),
                )
            sid = persist_snapshot_g3(conn, SnapshotPayloadG3(
                snapshot_uuid="g33-short", snapshot_ts=now, funding=-0.0001,
            ))
            trends = [
                TrendWindowG3(
                    symbol="SOL", window_minutes=5, pattern_type="slow_bleed",
                    consecutive_candles=3, trend_score=70.0, direction="DOWN",
                    details={"description": "3 red candles"},
                ),
            ]
            liquidity = LiquidityStateG3(
                primary_state="Capitulation",
                probabilities={"Capitulation": 0.85},
                factors={"funding": -0.0001, "oi_rising": True, "liquidations": 100},
            )
            cand = evaluate_symbol_candidate_g31(
                conn, symbol="SOL", trends=trends, liquidity=liquidity, snapshot_row=conn.execute(
                    "SELECT * FROM market_snapshots_g3 WHERE id = ?", (sid,),
                ).fetchone(),
            )
            self.assertNotEqual(cand.candidate_state, STATE_INSUFFICIENT)
            self.assertNotIn("Insufficient trend data", cand.rejection_reason or "")
            self.assertIsNotNone(cand.trend_coverage_pct)
            self.assertLess(cand.trend_coverage_pct or 0, 100.0)

    def test_coverage_cli_and_dashboard(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            sid = persist_snapshot_g3(conn, SnapshotPayloadG3(
                snapshot_uuid="g33-cli", snapshot_ts=int(time.time()), funding=-0.0001,
            ))
            trends = [
                TrendWindowG3(
                    symbol="SOL", window_minutes=15, pattern_type="slow_bleed",
                    consecutive_candles=8, trend_score=65.0, direction="DOWN",
                    details={"description": "8 red candles"},
                ),
            ]
            liquidity = LiquidityStateG3(
                primary_state="Capitulation",
                probabilities={"Capitulation": 0.8},
                factors={"funding": -0.0001, "oi_rising": True},
            )
            candidates = build_candidates_g31(
                conn, snapshot_id=sid, trends=trends, liquidity=liquidity,
            )
            patched = [
                self._patch_sol_coverage(c) if c.symbol == "SOL" else c
                for c in candidates
            ]
            persist_candidates_g31(conn, snapshot_id=sid, candidates=patched)

            report = format_candidate_coverage_report(conn, symbol="SOL")
            self.assertIn("Coverage", report)
            dash = trend_coverage_dashboard(conn, limit=5)
            self.assertEqual(dash["tab"], "Trend Coverage")

    @staticmethod
    def _patch_sol_coverage(c):
        if c.symbol != "SOL":
            return c
        from dataclasses import replace
        return replace(
            c,
            trend_coverage_pct=42.0,
            trend_windows_json='[{"window_minutes":5,"weight_pct":15,"available":true,"trend_score":50,"direction":"DOWN"}]',
            candidate_state=STATE_PROVISIONAL,
            rejection_reason=format_provisional_reason(42.0),
        )


if __name__ == "__main__":
    unittest.main()
