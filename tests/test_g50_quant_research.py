"""G5.0 Quant Research Analyst tests (research-only)."""

from __future__ import annotations

import json
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
from bot.research.market_events.signal_intelligence.quant_research_g50 import (
    fetch_latest_quant_report_g50,
    format_quant_report_cli_g50,
    format_quant_telegram_g50,
    quant_research_dashboard_g50,
    run_quant_research_g50,
)
from bot.research.market_events.signal_intelligence.research_dataset_g50 import (
    build_research_dataset_g50,
    dataset_hash_g50,
)
from bot.research.market_events.signal_intelligence.research_prompt_g50 import (
    SYSTEM_PROMPT_G50,
    build_research_prompt_g50,
    validate_research_json_g50,
)


class QuantResearchG50Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g50.db"
        configure_unit_test_db_isolation(self.db_path)
        self._env = patch.dict(os.environ, {
            "ME_G50_QUANT_RESEARCH": "true",
            "ME_G32_CANDIDATE_REPLAY": "true",
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def _seed_candidate(self, conn) -> None:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO market_snapshots_g3 (snapshot_uuid, snapshot_ts, recorder_status, created_at)
            VALUES ('g50-snap', ?, 'ok', ?)
            """,
            (now, now),
        )
        sid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        cand = CandidateG31(
            symbol="SOL", trend_score=60, market_score=62, liquidity_score=0.7,
            confidence=7.2, rr=2.3, btc_alignment="Neutral",
            funding_score=55, oi_score=58, volume_score=50, atr_score=45,
            fear_greed=30, candidate_state=STATE_REJECTED,
            rejection_reason="Market Score below threshold", direction="SHORT",
        )
        persist_candidates_g31(conn, snapshot_id=sid, candidates=[cand], candidate_ts=now)

    def test_schema_v38(self) -> None:
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v41", applied)
            self.assertEqual(SCHEMA_VERSION, 44)

    def test_dataset_builder(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            dataset = build_research_dataset_g50(conn, max_records=100, days=30)
            self.assertGreaterEqual(dataset["sample_size"], 1)
            rec = dataset["records"][0]
            self.assertEqual(rec["symbol"], "SOL")
            self.assertIn("market_score", rec)
            self.assertTrue(dataset_hash_g50(dataset))

    def test_prompt_builder(self) -> None:
        dataset = {"sample_size": 5, "records": [], "aggregate_stats": {"sample_size": 5}}
        prompt = build_research_prompt_g50(dataset)
        self.assertIn("quantitative", SYSTEM_PROMPT_G50.lower())
        self.assertIn("Questions:", prompt)
        self.assertIn("DATA:", prompt)
        self.assertLessEqual(len(prompt) // 4, 1200)

    def test_run_quant_research_deterministic(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            result = run_quant_research_g50(conn, force=True)
            conn.commit()
            self.assertIn(result["status"], ("deterministic_fallback", "quota_blocked:daily_limit 0/30", "claude"))
            report = result["report"]
            self.assertIn("top_factors", report)
            self.assertIn("confidence", report)
            latest = fetch_latest_quant_report_g50(conn)
            self.assertIsNotNone(latest)

    def test_telegram_and_cli_reports(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            run_quant_research_g50(conn, force=True)
            conn.commit()
            tg = format_quant_telegram_g50(conn)
            self.assertIn("Quant Research", tg)
            cli = format_quant_report_cli_g50(conn)
            self.assertIn("G5.0 Quant Research Report", cli)

    def test_dashboard_payload(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            run_quant_research_g50(conn, force=True)
            conn.commit()
            dash = quant_research_dashboard_g50(conn)
            self.assertEqual(dash["tab"], "Quant Research")
            self.assertIn("recent_reports", dash)

    def test_validate_json_schema(self) -> None:
        out = validate_research_json_g50({"confidence": 0.8, "sample_size": 10})
        self.assertIsInstance(out["top_factors"], list)
        self.assertEqual(out["sample_size"], 10)


if __name__ == "__main__":
    unittest.main()
