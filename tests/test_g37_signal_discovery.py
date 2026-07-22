"""G3.7 Signal Discovery Diagnostics tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    CandidateG31,
    STATE_REJECTED,
    persist_candidates_g31,
)
from bot.research.market_events.signal_intelligence.signal_discovery_g37 import (
    build_pipeline_trace_g37,
    format_top_blockers_g37,
    format_why_not_g37,
    market_score_stuck_audit_g37,
    pipeline_funnel_g37,
    recommend_thresholds_g37,
    score_distribution_g37,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    handle_market_events_command,
)


class SignalDiscoveryG37Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g37.db"
        configure_unit_test_db_isolation(self.db_path)
        self._env = patch.dict(os.environ, {"ME_G31_CANDIDATE_PIPELINE": "true"}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _seed_candidates(self, conn) -> None:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_snapshots_g3 (snapshot_uuid, snapshot_ts, recorder_status, created_at)
            VALUES ('g37-snap', ?, 'ok', ?)
            """,
            (now, now),
        )
        sid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        rows = [
            CandidateG31(
                symbol="BTC", trend_score=58, market_score=63, liquidity_score=81,
                confidence=8.6, rr=2.1, btc_alignment="Neutral",
                funding_score=50, oi_score=50, volume_score=60, atr_score=45,
                fear_greed=40, candidate_state=STATE_REJECTED,
                rejection_reason="RR 2.1 < 2.5", direction="LONG",
                score_source="per_symbol",
            ),
            CandidateG31(
                symbol="SOL", trend_score=62, market_score=58, liquidity_score=75,
                confidence=7.2, rr=2.8, btc_alignment="Neutral",
                funding_score=50, oi_score=50, volume_score=55, atr_score=45,
                fear_greed=40, candidate_state=STATE_REJECTED,
                rejection_reason="Market Score 58 < 65", direction="LONG",
                score_source="per_symbol",
            ),
            CandidateG31(
                symbol="ETH", trend_score=55, market_score=61, liquidity_score=72,
                confidence=7.8, rr=2.6, btc_alignment="Neutral",
                funding_score=50, oi_score=50, volume_score=50, atr_score=45,
                fear_greed=40, candidate_state=STATE_REJECTED,
                rejection_reason="Market Score 61 < 65", direction="LONG",
                score_source="per_symbol",
            ),
        ]
        persist_candidates_g31(conn, snapshot_id=sid, candidates=rows, candidate_ts=now)

    def test_schema_v41(self) -> None:
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v41", applied)
            self.assertGreaterEqual(SCHEMA_VERSION, 44)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(market_candidate_g31)").fetchall()}
            self.assertIn("pipeline_trace_json", cols)
            self.assertIn("score_source", cols)

    def test_pipeline_trace(self) -> None:
        row = {
            "symbol": "BTC", "trend_score": 58, "market_score": 63, "liquidity_score": 81,
            "confidence": 8.6, "rr": 2.1, "funding_score": 50, "volume_score": 60,
            "btc_alignment": "Neutral", "candidate_state": "rejected",
            "rejection_reason": "RR 2.1 < 2.5",
        }
        trace = build_pipeline_trace_g37(row)
        rr_gate = next(g for g in trace if g["gate"] == "RR")
        self.assertFalse(rr_gate["pass"])
        ms_gate = next(g for g in trace if g["gate"] == "Market Score")
        self.assertFalse(ms_gate["pass"])

    def test_why_not_btc(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidates(conn)
            conn.commit()
            text = format_why_not_g37(conn, "BTC")
            self.assertIn("не отправлен", text)
            self.assertIn("RR", text)

    def test_why_not_sol(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidates(conn)
            conn.commit()
        result = handle_market_events_command("/why-not SOL")
        self.assertTrue(result.ok)
        self.assertIn("SOL", result.reply_text)
        self.assertIn("Market Score", result.reply_text)

    def test_top_blockers(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidates(conn)
            conn.commit()
            text = format_top_blockers_g37(conn, hours=24)
            self.assertIn("Market Score", text)

    def test_funnel_and_distribution(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidates(conn)
            conn.commit()
            funnel = pipeline_funnel_g37(conn)
            self.assertEqual(funnel["universe"], 3)
            self.assertGreater(funnel["trend_ok"], 0)
            dist = score_distribution_g37(conn, field="market_score", hours=24)
            self.assertGreater(len(dist), 0)

    def test_recommendations(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidates(conn)
            conn.commit()
            recs = recommend_thresholds_g37(conn, hours=24)
            self.assertTrue(any(r["field"] == "Market Score" for r in recs))

    def test_score_not_stuck_after_per_symbol_fix(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidates(conn)
            conn.commit()
            audit = market_score_stuck_audit_g37(conn, hours=24)
            self.assertGreater(audit["std"], 0.5)
            self.assertIn("per_symbol", str(audit.get("score_sources", {})))

    def test_persist_trace_json(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidates(conn)
            conn.commit()
            row = conn.execute(
                "SELECT pipeline_trace_json, score_source FROM market_candidate_g31 WHERE symbol='BTC'",
            ).fetchone()
            self.assertIsNotNone(row["pipeline_trace_json"])
            self.assertEqual(row["score_source"], "per_symbol")


if __name__ == "__main__":
    unittest.main()
