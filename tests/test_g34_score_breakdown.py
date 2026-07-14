"""G3.4 score breakdown and calibration tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    CandidateG31,
    STATE_CANDIDATE,
    persist_candidates_g31,
)
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.recorder_g3 import (
    SnapshotPayloadG3,
    persist_snapshot_g3,
)
from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
    build_score_breakdown_g34,
    compute_confidence_breakdown,
    detect_score_conflicts,
    format_market_heatmap,
    format_score_breakdown_report,
    format_score_correlation_report,
    format_score_recommendations_report,
    persist_score_breakdown_g34,
    score_diagnostics_dashboard,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3


class ScoreBreakdownG34Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g34.db"
        self._env = patch.dict(os.environ, {"ME_G34_SCORE_BREAKDOWN": "true"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v33(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v37", applied)
            self.assertEqual(SCHEMA_VERSION, 37)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_score_breakdown_g34", tables)
            self.assertIn("market_score_conflicts_g34", tables)

    def test_confidence_breakdown_sums_to_target(self) -> None:
        rows = compute_confidence_breakdown(
            confidence=8.7,
            trend_score=75.0,
            liquidity_score=80.0,
            funding_score=65.0,
            oi_score=72.0,
            volume_score=60.0,
            atr_score=55.0,
            history_rate=0.82,
            claude_conf=8.0,
        )
        total = 2.0 + sum(r.contribution for r in rows)
        self.assertAlmostEqual(total, 8.7, delta=0.15)
        factors = {r.factor for r in rows}
        for name in ("Trend", "Liquidity", "Funding", "OI", "Volume", "ATR", "History", "Claude"):
            self.assertIn(name, factors)

    def test_conflict_detection(self) -> None:
        conf_rows = compute_confidence_breakdown(
            confidence=8.5,
            trend_score=80,
            liquidity_score=50,
            funding_score=70,
            oi_score=40,
            volume_score=60,
            atr_score=50,
            history_rate=0.5,
            claude_conf=8.0,
        )
        conflicts = detect_score_conflicts(
            confidence=8.5,
            market_score=55.0,
            conf_rows=conf_rows,
            ms_rows=(),
            funding_score=70,
            oi_score=40,
        )
        types = {c["conflict_type"] for c in conflicts}
        self.assertIn("high_conf_low_market", types)
        self.assertIn("funding_vs_oi", types)

    def test_persist_and_cli_report(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            sid = persist_snapshot_g3(conn, SnapshotPayloadG3(
                snapshot_uuid="g34-test", snapshot_ts=int(time.time()), funding=-0.0002,
            ))
            trend = TrendWindowG3(
                symbol="SOL", window_minutes=15, pattern_type="slow_bleed",
                consecutive_candles=10, trend_score=75.0, direction="DOWN",
                details={"description": "10 red candles"},
            )
            cand = CandidateG31(
                symbol="SOL",
                trend_score=75.0,
                market_score=61.0,
                liquidity_score=80.0,
                confidence=8.7,
                rr=2.8,
                btc_alignment="Neutral",
                funding_score=65.0,
                oi_score=72.0,
                volume_score=60.0,
                atr_score=55.0,
                fear_greed=35.0,
                candidate_state=STATE_CANDIDATE,
                rejection_reason=None,
                direction="SHORT",
                trend_coverage_pct=100.0,
                trend=trend,
            )
            liquidity = LiquidityStateG3(
                primary_state="Capitulation",
                probabilities={"Capitulation": 0.8},
                factors={"funding": -0.0002, "oi_rising": True},
            )
            persist_candidates_g31(
                conn, snapshot_id=sid, candidates=[cand], liquidity=liquidity, claude_conf=8.0,
            )
            row = conn.execute(
                "SELECT id FROM market_candidate_g31 WHERE symbol = 'SOL'",
            ).fetchone()
            bd_count = conn.execute(
                "SELECT COUNT(*) AS n FROM market_score_breakdown_g34 WHERE candidate_id = ?",
                (row["id"],),
            ).fetchone()
            self.assertGreater(int(bd_count["n"]), 0)

            report = format_score_breakdown_report(conn, symbol="SOL")
            self.assertIn("SOL", report)
            self.assertIn("Confidence", report)
            self.assertIn("Market Score", report)
            self.assertIn("Trend", report)

            corr = format_score_correlation_report(conn)
            self.assertIn("Score Correlation", corr)
            recs = format_score_recommendations_report(conn)
            self.assertIn("Recommendations", recs)
            heat = format_market_heatmap(conn, limit=3)
            self.assertIn("Heatmap", heat)
            dash = score_diagnostics_dashboard(conn)
            self.assertEqual(dash["tab"], "Score Diagnostics")


if __name__ == "__main__":
    unittest.main()
