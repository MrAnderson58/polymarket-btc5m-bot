"""Phase G.4.0.1 — Shadow pipeline end-to-end tests."""

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
from bot.research.market_events.signal_intelligence.shadow_g40 import (
    SHADOW_HEADER,
    create_shadow_signal_g40,
    persist_shadow_signal_g40,
)
from bot.research.market_events.signal_intelligence.shadow_pipeline_g401 import (
    ShadowPipelineError,
    build_shadow_trace_g401,
    execute_shadow_pipeline_g401,
    format_shadow_trace_g401,
    run_shadow_pipeline_g401,
    run_shadow_self_test_g401,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3


def _trend(symbol: str = "BTC") -> TrendWindowG3:
    return TrendWindowG3(
        symbol=symbol,
        window_minutes=15,
        pattern_type="slow_bleed",
        consecutive_candles=6,
        trend_score=40.0,
        direction="DOWN",
        details={},
    )


def _candidate(**kwargs) -> CandidateG31:
    base = dict(
        symbol="BTC",
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


class ShadowPipelineEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "shadow_pipeline.db"
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

    def _snap_id(self, conn) -> int:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_snapshots_g3 (
              snapshot_uuid, snapshot_ts, btc_price, created_at
            ) VALUES (?, ?, 65000.0, ?)
            """,
            (f"test-{now}", now, now),
        )
        conn.commit()
        return int(conn.execute("SELECT id FROM market_snapshots_g3 ORDER BY id DESC LIMIT 1").fetchone()["id"])

    def test_schema_v44_trace_table(self) -> None:
        with self._conn() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v44", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 44)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='market_shadow_pipeline_trace'",
            ).fetchone()
            self.assertIsNotNone(row)

    def test_persist_shadow_signal_alias(self) -> None:
        self.assertIs(create_shadow_signal_g40, persist_shadow_signal_g40)

    def test_trace_disabled_shadow(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_pipeline_g401 as pipe_mod
        with patch.object(pipe_mod, "G40_SHADOW_ENABLED", False):
            with self._conn() as conn:
                apply_migrations(conn)
                snap_id = self._snap_id(conn)
                r = build_shadow_trace_g401(conn, candidate=_candidate(), snapshot_id=snap_id)
                self.assertEqual(r.steps[1].step, "Shadow Enabled")
                self.assertEqual(r.steps[1].status, "NO")
                persist = next(s for s in r.steps if s.step == "Persist")
                self.assertEqual(persist.status, "SKIPPED")
                self.assertIn("ME_SHADOW_ENABLED", persist.reason or "")

    def test_trace_eligible_candidate(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_g40 as mod
        from bot.research.market_events.signal_intelligence import shadow_pipeline_g401 as pipe_mod
        with patch.object(pipe_mod, "G40_SHADOW_ENABLED", True):
            with patch.object(mod, "send_shadow_telegram_g40", return_value=True):
                with self._conn() as conn:
                    apply_migrations(conn)
                    snap_id = self._snap_id(conn)
                    r = execute_shadow_pipeline_g401(
                        conn,
                        candidate=_candidate(),
                        snapshot_id=snap_id,
                        cycle_context={},
                    )
                    self.assertEqual(r.outcome, "CREATED")
                    self.assertIsNotNone(r.signal_id)
                    steps = [s.step for s in r.steps]
                    self.assertEqual(
                        steps,
                        ["Candidate", "Shadow Enabled", "Thresholds", "Persist", "Telegram", "Followup scheduled"],
                    )
                    row = conn.execute(
                        "SELECT * FROM market_shadow_signals WHERE id = ?",
                        (r.signal_id,),
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertIn(SHADOW_HEADER, row["telegram_rendered"])
                    followup = next(s for s in r.steps if s.step == "Followup scheduled")
                    self.assertEqual(followup.status, "YES")

    def test_persist_failure_raises_when_enabled(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_g40 as mod
        from bot.research.market_events.signal_intelligence import shadow_pipeline_g401 as pipe_mod
        with patch.object(pipe_mod, "G40_SHADOW_ENABLED", True):
            with self._conn() as conn:
                apply_migrations(conn)
                snap_id = self._snap_id(conn)
                with patch.object(pipe_mod, "persist_shadow_signal_g40", side_effect=RuntimeError("db fail")):
                    with self.assertRaises(ShadowPipelineError):
                        execute_shadow_pipeline_g401(
                            conn,
                            candidate=_candidate(),
                            snapshot_id=snap_id,
                            cycle_context={},
                            send_telegram=False,
                        )

    def test_run_pipeline_persists_traces(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_g40 as mod
        from bot.research.market_events.signal_intelligence import shadow_pipeline_g401 as pipe_mod
        with patch.object(pipe_mod, "G40_SHADOW_ENABLED", True):
            with patch.object(mod, "send_shadow_telegram_g40", return_value=True):
                with self._conn() as conn:
                    apply_migrations(conn)
                    snap_id = self._snap_id(conn)
                    ts = int(time.time())
                    results = run_shadow_pipeline_g401(
                        conn,
                        snapshot_id=snap_id,
                        candidates=[_candidate(), _candidate(symbol="ETH", trend=_trend("ETH"))],
                        candidate_ts=ts,
                    )
                    self.assertEqual(len(results), 2)
                    trace_rows = conn.execute(
                        "SELECT COUNT(*) AS n FROM market_shadow_pipeline_trace WHERE candidate_ts = ?",
                        (ts,),
                    ).fetchone()
                    self.assertEqual(int(trace_rows["n"]), 2)

    def test_self_test_pass(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            ok, msg = run_shadow_self_test_g401(conn)
            self.assertTrue(ok, msg)
            self.assertEqual(msg, "PASS")

    def test_shadow_trace_format(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_g40 as mod
        from bot.research.market_events.signal_intelligence import shadow_pipeline_g401 as pipe_mod
        with patch.object(pipe_mod, "G40_SHADOW_ENABLED", True):
            with patch.object(mod, "send_shadow_telegram_g40", return_value=True):
                with self._conn() as conn:
                    apply_migrations(conn)
                    snap_id = self._snap_id(conn)
                    ts = int(time.time())
                    conn.execute(
                        """
                        INSERT INTO market_candidate_g31 (
                          candidate_ts, snapshot_id, symbol, trend_score, market_score,
                          liquidity_score, confidence, rr, btc_alignment, funding_score,
                          oi_score, volume_score, atr_score, fear_greed, candidate_state,
                          rejection_reason, direction, created_at
                        ) VALUES (?, ?, 'BTC', 40, 31, 42, 5.8, 1.7, 'Neutral', 50, 50, 18, 50, 50,
                                  'rejected', 'test', 'SHORT', ?)
                        """,
                        (ts, snap_id, ts),
                    )
                    conn.commit()
                    run_shadow_pipeline_g401(
                        conn,
                        snapshot_id=snap_id,
                        candidates=[_candidate()],
                        candidate_ts=ts,
                    )
                    conn.commit()
                    text = format_shadow_trace_g401(conn, symbol="BTC")
                    self.assertIn("BTC", text)
                    self.assertIn("Candidate", text)
                    self.assertIn("Shadow Enabled", text)
                    self.assertIn("Thresholds", text)


if __name__ == "__main__":
    unittest.main()
