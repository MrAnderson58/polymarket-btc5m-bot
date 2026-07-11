"""F.0 AI prompt and strict JSON response tests."""

from __future__ import annotations

import json
import unittest

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.ai_context_f0 import (
    build_f0_context_bundle,
    build_f0_prompt,
)
from bot.research.market_events.signal_intelligence.ai_f0 import (
    analyze_f0_deterministic,
    persist_f0_analysis,
    run_f0_ai_analysis,
)
from bot.research.market_events.signal_intelligence.ai_schema_f0 import (
    F0AnalysisResponse,
    VALID_BIAS,
    parse_f0_response,
)
from bot.research.market_events.signal_intelligence.config import PROMPT_VERSION_F0
from tests.f0_test_utils import conn_ctx, make_db, seed_event


class AiPromptV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_parse_valid_json(self) -> None:
        raw = {
            "bias": "FADE",
            "confidence": 0.82,
            "continuation_probability": 0.3,
            "reversal_probability": 0.7,
            "summary_ru": "Тест",
            "summary_en": "Test",
        }
        resp = parse_f0_response(raw)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.bias, "FADE")

    def test_parse_json_string(self) -> None:
        raw = json.dumps({
            "bias": "CONTINUATION",
            "confidence": 0.6,
            "continuation_probability": 0.65,
            "reversal_probability": 0.35,
            "summary_ru": "ru",
            "summary_en": "en",
        })
        resp = parse_f0_response(raw)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.bias, "CONTINUATION")

    def test_invalid_bias_becomes_neutral(self) -> None:
        resp = parse_f0_response({"bias": "INVALID", "confidence": 0.5})
        self.assertEqual(resp.bias, "NEUTRAL")

    def test_invalid_json_returns_none(self) -> None:
        self.assertIsNone(parse_f0_response("not json"))

    def test_confidence_clamped(self) -> None:
        resp = parse_f0_response({"bias": "WAIT", "confidence": 1.5})
        self.assertLessEqual(resp.confidence, 1.0)

    def test_response_to_dict(self) -> None:
        r = F0AnalysisResponse("FADE", 0.8, 0.2, 0.8, "ru", "en")
        d = r.to_dict()
        self.assertEqual(set(d.keys()), {
            "bias", "confidence", "continuation_probability",
            "reversal_probability", "summary_ru", "summary_en",
        })

    def test_valid_bias_set(self) -> None:
        self.assertIn("FADE", VALID_BIAS)
        self.assertIn("WAIT", VALID_BIAS)

    def test_build_f0_prompt_is_json(self) -> None:
        bundle = {
            "market_event": {"symbol": "SUI", "return_pct": -8.4},
            "telegram_context": [],
            "f0": {"exchange": None, "opportunity_v2": {}, "exhaustion": {}, "multitimeframe": []},
        }
        prompt = build_f0_prompt(bundle)
        data = json.loads(prompt)
        self.assertIn("required_fields", data)
        self.assertIn("bias", data["required_fields"])

    def test_prompt_no_free_text_instruction(self) -> None:
        bundle = {"market_event": {}, "f0": {}}
        prompt = build_f0_prompt(bundle)
        self.assertIn("JSON only", prompt)

    def test_analyze_f0_deterministic(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=-8.4)
            resp = analyze_f0_deterministic(conn, eid)
            self.assertIn(resp.bias, VALID_BIAS)
            self.assertGreater(resp.confidence, 0)

    def test_high_exhaustion_favors_fade(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            conn.execute(
                """
                INSERT INTO market_events_exhaustion (
                  symbol, event_ts, event_type, exhaustion_score, reasons_json,
                  dedup_key, created_at, source_event_id
                ) VALUES ('SUI', 1, 'TREND_EXHAUSTION', 85, '[]', 'exh-ai', 1, ?)
                """,
                (eid,),
            )
            resp = analyze_f0_deterministic(conn, eid)
            self.assertEqual(resp.bias, "FADE")

    def test_persist_and_run_f0_ai(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            resp = run_f0_ai_analysis(conn, eid)
            row = conn.execute(
                "SELECT prompt_version, bias FROM market_event_ai_analyses_f0 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertEqual(row["prompt_version"], PROMPT_VERSION_F0)
            self.assertEqual(row["bias"], resp.bias)

    def test_build_f0_context_bundle(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            bundle = build_f0_context_bundle(conn, eid)
            self.assertIn("f0", bundle)

    def test_response_json_stored(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            resp = F0AnalysisResponse("NEUTRAL", 0.5, 0.5, 0.5, "ru", "en")
            persist_f0_analysis(conn, eid, resp)
            row = conn.execute(
                "SELECT response_json FROM market_event_ai_analyses_f0 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            data = json.loads(row["response_json"])
            self.assertEqual(data["bias"], "NEUTRAL")


if __name__ == "__main__":
    unittest.main()
