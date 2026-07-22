"""Phase G.4.0 — Shadow Signal Lane tests."""

from __future__ import annotations

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
    G40_SHADOW_MIN_CONFIDENCE,
    G40_SHADOW_MIN_MARKET_SCORE,
    G40_SHADOW_MIN_VOLUME,
)
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.shadow_g40 import (
    SHADOW_HEADER,
    SHADOW_PROFILE,
    compute_grade_g40,
    create_shadow_signal_g40,
    format_production_rejection_shadow_g40,
    format_shadow_telegram_g40,
    is_shadow_only_g40,
    passes_shadow_g40,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3


def _trend() -> TrendWindowG3:
    return TrendWindowG3(
        symbol="SOL",
        window_minutes=15,
        pattern_type="slow_bleed",
        consecutive_candles=6,
        trend_score=40.0,
        direction="DOWN",
        details={"description": "6 red candles"},
    )


def _candidate(**kwargs) -> CandidateG31:
    base = dict(
        symbol="SOL",
        trend_score=40.0,
        market_score=31.0,
        liquidity_score=42.0,
        confidence=5.8,
        rr=1.7,
        btc_alignment="Neutral",
        funding_score=50.0,
        oi_score=50.0,
        volume_score=18.0,
        atr_score=50.0,
        fear_greed=50.0,
        candidate_state="rejected",
        rejection_reason="Market Score 31 < 65",
        direction="SHORT",
        trend_coverage_pct=100.0,
        trend_windows_json="[]",
        trend=_trend(),
    )
    base.update(kwargs)
    return CandidateG31(**base)


class ShadowG40Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g40.db"
        self._env = patch.dict(
            os.environ,
            {"ME_SHADOW_ENABLED": "true", "ME_G31_CANDIDATE_PIPELINE": "true"},
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_schema_v44(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v44", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 44)
            for table in ("market_shadow_signals", "market_shadow_horizons", "market_learning_dataset"):
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                self.assertIsNotNone(row, table)

    def test_shadow_thresholds(self) -> None:
        self.assertEqual(SHADOW_PROFILE.min_confidence, G40_SHADOW_MIN_CONFIDENCE)
        self.assertEqual(SHADOW_PROFILE.min_market_score, G40_SHADOW_MIN_MARKET_SCORE)
        self.assertEqual(SHADOW_PROFILE.min_volume, G40_SHADOW_MIN_VOLUME)

    def test_shadow_only_candidate(self) -> None:
        c = _candidate()
        self.assertTrue(passes_shadow_g40(c))
        self.assertTrue(is_shadow_only_g40(c))

    def test_production_unchanged(self) -> None:
        from bot.research.market_events.signal_intelligence.config import (
            G3_MIN_CONFIDENCE as C,
            G3_MIN_MARKET_SCORE as M,
        )
        self.assertEqual(C, G3_MIN_CONFIDENCE)
        self.assertEqual(M, G3_MIN_MARKET_SCORE)

    def test_production_rejection_text(self) -> None:
        text = format_production_rejection_shadow_g40(_candidate())
        self.assertIn("Почему не прошёл Production", text)
        self.assertIn("7.5", text)
        self.assertIn("65", text)

    def test_shadow_telegram_format(self) -> None:
        text = format_shadow_telegram_g40(
            candidate=_candidate(),
            claude_summary="Test Claude summary",
        )
        self.assertIn(SHADOW_HEADER, text)
        self.assertIn("SOL SHORT", text)
        self.assertIn("5.8", text)
        self.assertIn("Claude", text)

    def test_grade_logic(self) -> None:
        self.assertEqual(
            compute_grade_g40(
                pnl_pct=4.0, max_profit=4.5, max_drawdown=-0.5, rr=4.2,
                holding_sec=3600, tp1_hit=True, tp2_hit=True, tp3_hit=True, sl_hit=False,
            ),
            "A+",
        )
        self.assertEqual(
            compute_grade_g40(
                pnl_pct=-2.5, max_profit=0.1, max_drawdown=-2.5, rr=1.3,
                holding_sec=600, tp1_hit=False, tp2_hit=False, tp3_hit=False, sl_hit=True,
            ),
            "F",
        )

    def test_persist_shadow_signal(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_g40 as mod
        with patch.object(mod, "G40_SHADOW_ENABLED", True):
            with self._conn() as conn:
                apply_migrations(conn)
                conn.execute(
                    """
                    INSERT INTO market_snapshots_g3 (
                      snapshot_uuid, snapshot_ts, sol_price, created_at
                    ) VALUES ('t', ?, 150.0, ?)
                    """,
                    (int(time.time()), int(time.time())),
                )
                conn.commit()
                snap_id = int(conn.execute("SELECT id FROM market_snapshots_g3").fetchone()["id"])
                sig = create_shadow_signal_g40(
                    conn, snapshot_id=snap_id, candidate=_candidate(),
                )
                conn.commit()
                self.assertIsNotNone(sig)
                assert sig is not None
                row = conn.execute(
                    "SELECT * FROM market_shadow_signals WHERE id = ?",
                    (sig.signal_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(float(row["confidence"]), 5.8)
                self.assertIn(SHADOW_HEADER, row["telegram_rendered"])


if __name__ == "__main__":
    unittest.main()
