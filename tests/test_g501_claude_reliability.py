"""G5.0.1 Claude reliability and research quality tests."""

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
from bot.research.market_events.signal_intelligence.claude_json_parser_g501 import (
    extract_json_from_claude_text,
    repair_json,
)
from bot.research.market_events.signal_intelligence.deterministic_research_g501 import (
    build_deterministic_research_g501,
    compute_research_score_g501,
)
from bot.research.market_events.signal_intelligence.quant_research_g50 import (
    format_quant_debug_g50,
    format_quant_debug_telegram_g50,
    run_quant_research_g50,
)
from bot.research.market_events.signal_intelligence.research_dataset_g50 import (
    build_research_dataset_g50,
)
from bot.research.market_events.signal_intelligence.research_prompt_g50 import (
    MAX_PROMPT_TOKENS,
    build_research_prompt_g50,
    estimate_prompt_tokens,
    validate_research_json_g50,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    handle_market_events_command,
)


class ClaudeReliabilityG501Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g501.db"
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
            VALUES ('g501-snap', ?, 'ok', ?)
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
            self.assertEqual(SCHEMA_VERSION, 41)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(market_events_quant_reports_g50)").fetchall()}
            self.assertIn("raw_response", cols)
            self.assertIn("research_score", cols)

    def test_parser_formats(self) -> None:
        payload = {"top_factors": [], "confidence": 0.7, "sample_size": 10}
        bare = json.dumps(payload)
        for text in (
            bare,
            f"```json\n{bare}\n```",
            f"text\n\n{bare}",
            f"<analysis>\n{bare}\n</analysis>",
        ):
            parsed, err = extract_json_from_claude_text(text)
            self.assertIsNone(err, msg=text[:40])
            self.assertEqual(parsed["confidence"], 0.7)

    def test_repair_json_trailing_comma(self) -> None:
        broken = '{"top_factors": [], "confidence": 0.5,}'
        repaired = repair_json(broken)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["confidence"], 0.5)

    def test_compact_prompt_under_budget(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            dataset = build_research_dataset_g50(conn, max_records=200, days=30)
            prompt = build_research_prompt_g50(dataset, conn=conn)
            tokens = estimate_prompt_tokens(prompt)
            self.assertLessEqual(tokens, MAX_PROMPT_TOKENS)

    def test_deterministic_fallback_uses_g4_context(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            dataset = build_research_dataset_g50(conn, max_records=100, days=30)
            report = build_deterministic_research_g501(conn, dataset=dataset)
            self.assertIn("missing_info", report)
            self.assertTrue(
                report["top_factors"] or report["research_improvements"] or report["tomorrow_hypotheses"],
            )
            self.assertGreater(report["research_score"], 2.0)

    def test_research_score_ranges(self) -> None:
        claude = compute_research_score_g501("claude", 0.85, 200, retries=0)
        fallback = compute_research_score_g501("deterministic_fallback", 0.5, 50, retries=0)
        raw = compute_research_score_g501("claude", 0.5, 50, retries=2, raw_only=True)
        self.assertGreater(claude, fallback)
        self.assertAlmostEqual(raw, 2.1)

    def test_run_persists_raw_and_score(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            result = run_quant_research_g50(conn, force=True)
            conn.commit()
            row = conn.execute(
                """
                SELECT research_score, missing_info_json, debug_json
                FROM market_events_quant_reports_g50 ORDER BY id DESC LIMIT 1
                """,
            ).fetchone()
            self.assertIsNotNone(row["research_score"])
            self.assertIsNotNone(row["missing_info_json"])
            self.assertIn("missing_info", result["report"])
            self.assertIn("research_score", result)

    def test_quant_debug_cli(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            run_quant_research_g50(conn, force=True)
            conn.commit()
            text = format_quant_debug_g50(conn)
            self.assertIn("Prompt", text)
            self.assertIn("Extracted JSON", text)
            self.assertIn("Retries", text)

    def test_research_debug_telegram(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            self._seed_candidate(conn)
            run_quant_research_g50(conn, force=True)
            conn.commit()
        result = handle_market_events_command("/research-debug")
        self.assertTrue(result.ok)
        self.assertIn("Research score", result.reply_text)

    def test_validate_json_includes_missing_info(self) -> None:
        out = validate_research_json_g50({"confidence": 0.6})
        self.assertIsInstance(out["missing_info"], list)


if __name__ == "__main__":
    unittest.main()
