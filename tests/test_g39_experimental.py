"""Phase G.3.9 — Experimental Signal Calibration tests."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candidate_g31 import CandidateG31
from bot.research.market_events.signal_intelligence.config import (
    G3_MIN_CONFIDENCE,
    G3_MIN_MARKET_SCORE,
    G39_MIN_CONFIDENCE,
    G39_MIN_MARKET_SCORE,
)
from bot.research.market_events.signal_intelligence.experimental_g39 import (
    EXPERIMENTAL_HEADER,
    EXPERIMENTAL_PROFILE,
    PRODUCTION_PROFILE,
    evaluate_experimental_signal_g39,
    format_production_rejection_g39,
    format_threshold_simulator_g39,
    is_experimental_only_g39,
    passes_profile_g39,
    pick_experimental_candidate_g39,
    run_threshold_simulator_g39,
)
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3


def _trend() -> TrendWindowG3:
    return TrendWindowG3(
        symbol="SOL",
        window_minutes=15,
        pattern_type="slow_bleed",
        consecutive_candles=6,
        trend_score=55.0,
        direction="DOWN",
        details={"description": "6 red candles"},
    )


def _candidate(**kwargs) -> CandidateG31:
    base = dict(
        symbol="SOL",
        trend_score=55.0,
        market_score=47.0,
        liquidity_score=55.0,
        confidence=6.3,
        rr=2.0,
        btc_alignment="Neutral",
        funding_score=50.0,
        oi_score=50.0,
        volume_score=45.0,
        atr_score=50.0,
        fear_greed=50.0,
        candidate_state="rejected",
        rejection_reason="Market Score 47 < 65",
        direction="SHORT",
        trend_coverage_pct=100.0,
        trend_windows_json="[]",
        trend=_trend(),
    )
    base.update(kwargs)
    return CandidateG31(**base)


class ExperimentalG39Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g39.db"
        self._env = patch.dict(
            os.environ,
            {
                "ME_G3_EXPERIMENTAL_MODE": "true",
                "ME_G31_CANDIDATE_PIPELINE": "true",
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v42(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v42", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 44)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='market_experimental_signals_g39'",
            ).fetchone()
            self.assertIsNotNone(row)

    def test_production_thresholds_unchanged(self) -> None:
        self.assertEqual(PRODUCTION_PROFILE.min_confidence, G3_MIN_CONFIDENCE)
        self.assertEqual(PRODUCTION_PROFILE.min_market_score, G3_MIN_MARKET_SCORE)

    def test_experimental_only_candidate(self) -> None:
        c = _candidate()
        self.assertTrue(passes_profile_g39(c, EXPERIMENTAL_PROFILE))
        self.assertFalse(passes_profile_g39(c, PRODUCTION_PROFILE))
        self.assertTrue(is_experimental_only_g39(c))

    def test_production_rejection_format(self) -> None:
        text = format_production_rejection_g39(_candidate())
        self.assertIn("Production rejected because", text)
        self.assertIn("Market Score", text)
        self.assertIn("65", text)
        self.assertIn("Confidence", text)
        self.assertIn("7.5", text)

    def test_pick_experimental_candidate(self) -> None:
        weak = _candidate(confidence=5.0, market_score=30.0, rr=1.5, liquidity_score=40.0)
        good = _candidate()
        picked = pick_experimental_candidate_g39([weak, good])
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked.symbol, "SOL")

    def test_experimental_thresholds(self) -> None:
        self.assertEqual(EXPERIMENTAL_PROFILE.min_confidence, G39_MIN_CONFIDENCE)
        self.assertEqual(EXPERIMENTAL_PROFILE.min_market_score, G39_MIN_MARKET_SCORE)

    def test_persist_experimental_signal(self) -> None:
        from bot.research.market_events.signal_intelligence import experimental_g39 as mod
        liquidity = LiquidityStateG3(
            primary_state="Capitulation",
            probabilities={"Capitulation": 0.55, "Neutral": 0.45},
            factors={"funding": -0.0001, "oi_rising": True},
        )
        with patch.object(mod, "G39_EXPERIMENTAL_MODE", True), patch.object(
            mod, "send_experimental_telegram_g39", return_value=False,
        ):
            with self._conn() as conn:
                apply_migrations(conn)
                conn.execute(
                    """
                    INSERT INTO market_snapshots_g3 (
                      snapshot_uuid, snapshot_ts, sol_price, created_at
                    ) VALUES ('test', ?, 150.0, ?)
                    """,
                    (int(time.time()), int(time.time())),
                )
                conn.commit()
                snap_id = int(conn.execute("SELECT id FROM market_snapshots_g3").fetchone()["id"])
                signal = evaluate_experimental_signal_g39(
                    conn,
                    snapshot_id=snap_id,
                    candidates=[_candidate()],
                    liquidity=liquidity,
                )
                conn.commit()
                self.assertIsNotNone(signal)
                assert signal is not None
                row = conn.execute(
                    "SELECT * FROM market_experimental_signals_g39 WHERE id = ?",
                    (signal.signal_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertIn(EXPERIMENTAL_HEADER.split("\n")[0], row["telegram_rendered"])
                rejection = json.loads(row["production_rejection_json"])
                self.assertTrue(len(rejection) > 0)

    def test_threshold_simulator_output(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            text = format_threshold_simulator_g39(conn, days=7)
            self.assertIn("Threshold Simulator", text)
            self.assertIn("Production", text)
            self.assertIn("Experimental", text)
            sim = run_threshold_simulator_g39(conn, days=7)
            self.assertIn("recommendation", sim)


if __name__ == "__main__":
    unittest.main()
