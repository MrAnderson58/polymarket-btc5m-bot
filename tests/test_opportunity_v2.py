"""F.0 opportunity score v2 tests."""

from __future__ import annotations

import json
import unittest

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.opportunity_v2 import (
    compute_opportunity_score_v2,
    format_score_breakdown,
    persist_opportunity_score_v2,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


class OpportunityV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_exhaustion(self, conn, event_id: int, score: float = 75.0) -> None:
        conn.execute(
            """
            INSERT INTO market_events_exhaustion (
              symbol, event_ts, event_type, exhaustion_score, reasons_json,
              dedup_key, created_at, source_event_id
            ) VALUES ('SUI', 1, 'TREND_EXHAUSTION', ?, '[]', ?, 1, ?)
            """,
            (score, f"exh-{event_id}", event_id),
        )

    def _seed_context(self, conn, event_id: int, *, funding: float = 0.05) -> None:
        conn.execute(
            """
            INSERT INTO market_event_exchange_context (
              event_id, venue, symbol, funding, open_interest, created_at
            ) VALUES (?, 'binance', 'SUIUSDT', ?, 1000000, 1)
            """,
            (event_id, funding),
        )

    def _seed_mtf(self, conn, event_id: int, n: int = 2) -> None:
        for i in range(n):
            conn.execute(
                """
                INSERT INTO market_events_multitimeframe (
                  detector_id, symbol, event_ts, window_minutes, return_pct,
                  atr_multiple, volume_multiple, direction, source_event_id,
                  dedup_key, created_at
                ) VALUES (?, 'SUI', ?, 5, 3.0, 2.0, 1.5, 'UP', ?, ?, 1)
                """,
                (f"SHOCK_M5" if i == 0 else "SHOCK_M10", 100 + i, event_id, f"mtf-{event_id}-{i}"),
            )

    def test_score_has_breakdown_keys(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            result = compute_opportunity_score_v2(conn, event_id=eid)
            for key in ("History", "Liquidity", "Volatility", "Telegram", "News"):
                self.assertIn(key, result["breakdown"])

    def test_funding_component(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_context(conn, eid, funding=0.08)
            result = compute_opportunity_score_v2(conn, event_id=eid)
            self.assertGreater(result["breakdown"]["Funding"], 0)

    def test_oi_component(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_context(conn, eid)
            result = compute_opportunity_score_v2(conn, event_id=eid)
            self.assertEqual(result["breakdown"]["OI"], 5.0)

    def test_exhaustion_component(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_exhaustion(conn, eid, score=80)
            result = compute_opportunity_score_v2(conn, event_id=eid)
            self.assertAlmostEqual(result["breakdown"]["Trend exhaustion"], 16.0)

    def test_cross_exchange_from_mtf(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_mtf(conn, eid, n=3)
            result = compute_opportunity_score_v2(conn, event_id=eid)
            self.assertEqual(result["breakdown"]["Cross exchange confirmation"], 9.0)

    def test_score_capped_at_100(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_context(conn, eid, funding=0.5)
            self._seed_exhaustion(conn, eid, score=100)
            self._seed_mtf(conn, eid, n=5)
            result = compute_opportunity_score_v2(conn, event_id=eid)
            self.assertLessEqual(result["score"], 100.0)

    def test_persist_opportunity_score_v2(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            score = persist_opportunity_score_v2(conn, event_id=eid)
            row = conn.execute(
                "SELECT score, score_breakdown_json FROM market_events_opportunity_scores_v2 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            self.assertAlmostEqual(float(row["score"]), score)
            breakdown = json.loads(row["score_breakdown_json"])
            self.assertIn("History", breakdown)

    def test_format_score_breakdown(self) -> None:
        text = format_score_breakdown({"History": 22.0, "Funding": 8.0})
        self.assertIn("History", text)
        self.assertIn("+22", text)

    def test_missing_event_returns_zero(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            result = compute_opportunity_score_v2(conn, event_id=99999)
            self.assertEqual(result["score"], 0.0)

    def test_breakdown_json_roundtrip(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            persist_opportunity_score_v2(conn, event_id=eid)
            row = conn.execute(
                "SELECT score_breakdown_json FROM market_events_opportunity_scores_v2 WHERE event_id = ?",
                (eid,),
            ).fetchone()
            data = json.loads(row["score_breakdown_json"])
            self.assertIsInstance(data, dict)

    def test_score_positive_for_event(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=-8.4)
            result = compute_opportunity_score_v2(conn, event_id=eid)
            self.assertGreater(result["score"], 0)


if __name__ == "__main__":
    unittest.main()
