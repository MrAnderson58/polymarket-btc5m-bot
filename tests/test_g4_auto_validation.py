"""G4 auto validation engine tests (research-only)."""

from __future__ import annotations

import os
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
    build_validation_daily_telegram_g4,
    format_false_accepts_report_g4,
    format_false_rejects_report_g4,
    format_feature_importance_report_g4,
    format_validation_report_g4,
    run_validation_cycle_g4,
    sync_validation_records_g4,
    validation_dashboard_g4,
)
from bot.research.market_events.signal_intelligence.candidate_g31 import (
    CandidateG31,
    STATE_REJECTED,
    persist_candidates_g31,
)
from bot.research.market_events.signal_intelligence.threshold_optimizer_g42 import (
    format_optimizer_report_g42,
    run_threshold_optimizer_g42,
)


class AutoValidationG4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g4.db"
        configure_unit_test_db_isolation(self.db_path)
        self._env = patch.dict(os.environ, {
            "ME_G4_AUTO_VALIDATION": "true",
            "ME_G4_VALIDATION_INTERVAL_SEC": "0",
            "ME_G32_CANDIDATE_REPLAY": "true",
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def test_schema_v36(self) -> None:
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v37", applied)
            self.assertEqual(SCHEMA_VERSION, 40)

    def _seed_rejected_with_outcome(self, conn) -> None:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_snapshots_g3 (
              snapshot_uuid, snapshot_ts, recorder_status, created_at
            ) VALUES ('g4-test-snap', ?, 'ok', ?)
            """,
            (now, now),
        )
        sid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        cand = CandidateG31(
            symbol="SOL",
            direction="SHORT",
            confidence=7.1,
            market_score=62.0,
            liquidity_score=0.72,
            rr=2.4,
            funding_score=55.0,
            oi_score=60.0,
            trend_score=58.0,
            volume_score=50.0,
            atr_score=45.0,
            fear_greed=35.0,
            btc_alignment="Neutral",
            candidate_state=STATE_REJECTED,
            rejection_reason="Market Score below threshold",
        )
        persist_candidates_g31(conn, snapshot_id=sid, candidates=[cand], candidate_ts=now)
        row = conn.execute(
            "SELECT id FROM market_candidate_g31 WHERE symbol = 'SOL' ORDER BY id DESC LIMIT 1",
        ).fetchone()
        cid = int(row["id"])
        oid = conn.execute(
            "SELECT id FROM market_candidate_outcomes_g32 WHERE candidate_id = ?",
            (cid,),
        ).fetchone()["id"]
        conn.execute(
            """
            UPDATE market_candidate_outcomes_g32 SET
              max_profit_pct = 2.5, would_hit_tp = 1, replay_status = 'COMPLETE', updated_at = ?
            WHERE id = ?
            """,
            (now, oid),
        )

    def test_ingest_and_validation_cycle(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_rejected_with_outcome(conn)
            n = sync_validation_records_g4(conn)
            self.assertGreaterEqual(n, 1)
            stats = run_validation_cycle_g4(conn, days=7)
            conn.commit()
            self.assertGreaterEqual(stats.ingested, 0)
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM market_validation_records_g4",
            ).fetchone()
            self.assertGreaterEqual(int(row["n"]), 1)

    def test_factor_importance_and_reports(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_rejected_with_outcome(conn)
            run_validation_cycle_g4(conn, days=7)
            conn.commit()
            self.assertIn("G4 Feature Importance", format_feature_importance_report_g4(conn))
            self.assertIn("G4 Validation Report", format_validation_report_g4(conn, days=7))
            self.assertIn("G4 False Reject", format_false_rejects_report_g4(conn))

    def test_optimizer_v2_per_symbol(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_rejected_with_outcome(conn)
            run_validation_cycle_g4(conn, days=7)
            conn.commit()
            scenarios = run_threshold_optimizer_g42(conn, days=7)
            report = format_optimizer_report_g42(conn, days=7)
            self.assertIn("G4 Optimizer", report)

    def test_dashboard_payload(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_rejected_with_outcome(conn)
            run_validation_cycle_g4(conn, days=7)
            conn.commit()
            dash = validation_dashboard_g4(conn, days=7)
            self.assertEqual(dash["tab"], "Validation")
            self.assertIn("factor_importance", dash)
            self.assertIn("optimizer", dash)

    def test_daily_telegram_report(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_rejected_with_outcome(conn)
            run_validation_cycle_g4(conn, days=7)
            conn.commit()
            msg = build_validation_daily_telegram_g4(conn)
            self.assertIn("G4 Validation Report", msg)
            self.assertIn("Win Rate", msg)

    def test_false_accept_report_renders(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_validation_records_g4 (
                  source_type, source_id, symbol, direction, is_win, pnl_pct, rr,
                  confidence, market_score, liquidity_score, factors_json, outcome_ts, created_at
                ) VALUES ('g3_signal', 1, 'BTC', 'LONG', 0, -1.2, 2.5, 8.0, 70, 0.8, ?, ?, ?)
                """,
                (json.dumps({"Funding": 80, "Trend": 75}), now, now),
            )
            run_validation_cycle_g4(conn, days=7)
            conn.commit()
            report = format_false_accepts_report_g4(conn)
            self.assertIn("G4 False Accept", report)


if __name__ == "__main__":
    unittest.main()
