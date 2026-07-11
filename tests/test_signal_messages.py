"""F.0 Telegram message format tests."""

from __future__ import annotations

import json
import unittest

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.market_event_alerts import PAPER_LABEL
from bot.research.market_events.signal_intelligence.opportunity_v2 import persist_opportunity_score_v2
from bot.research.market_events.signal_intelligence.telegram_f0 import (
    format_entry_f0,
    format_exit_f0,
    format_score_block,
    format_shock_f0,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


class SignalMessagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_score(self, conn, event_id: int) -> None:
        conn.execute(
            """
            INSERT INTO market_events_opportunity_scores_v2 (event_id, score, score_breakdown_json, created_at)
            VALUES (?, 87, ?, 1)
            """,
            (event_id, json.dumps({
                "History": 22, "Funding": 8, "Telegram": 16,
                "Liquidity": 18, "Trend exhaustion": 19,
            })),
        )

    def test_shock_format_header(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=8.4)
            text = format_shock_f0(conn, eid)
            self.assertIn("🚨 SHOCK", text)
            self.assertIn("SUI", text)

    def test_shock_includes_paper_label(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            text = format_shock_f0(conn, eid)
            self.assertIn(PAPER_LABEL, text)

    def test_shock_score_block(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_score(conn, eid)
            text = format_shock_f0(conn, eid)
            self.assertIn("Score", text)
            self.assertIn("87", text)

    def test_shock_breakdown_keys(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_score(conn, eid)
            text = format_shock_f0(conn, eid)
            for key in ("History", "Funding", "Telegram", "Liquidity"):
                self.assertIn(key, text)

    def test_shock_ai_waiting_r2(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            text = format_shock_f0(conn, eid)
            self.assertIn("Waiting R2", text)

    def test_shock_return_pct(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn, ret=-8.4)
            text = format_shock_f0(conn, eid)
            self.assertIn("-8.4%", text)

    def test_entry_format(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            text = format_entry_f0(
                conn, event_id=eid, symbol="SUI", reversal_variant="R2",
                entry=1.23, stop=1.18, target=1.35,
            )
            self.assertIn("✅ ENTRY", text)
            self.assertIn("R2", text)
            self.assertIn("1.23", text)
            self.assertIn(PAPER_LABEL, text)

    def test_exit_format(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            text = format_exit_f0(
                conn, event_id=eid, symbol="SUI", pnl_pct=3.4,
                holding_min=18, exit_variant="Exit B", ai_correct=True,
            )
            self.assertIn("🏁 RESULT", text)
            self.assertIn("+3.4%", text)
            self.assertIn("18m", text)
            self.assertIn("AI Correct", text)

    def test_exit_ai_partial(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            text = format_exit_f0(
                conn, event_id=eid, symbol="SUI", pnl_pct=-1.0,
                holding_min=5, exit_variant="Exit A", ai_correct=False,
            )
            self.assertIn("AI Partial", text)

    def test_format_score_block(self) -> None:
        text = format_score_block({"History": 22, "Funding": 8}, 83)
        self.assertIn("Score", text)
        self.assertIn("83", text)

    def test_shock_funding_high(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            self._seed_score(conn, eid)
            conn.execute(
                """
                INSERT INTO market_event_exchange_context (event_id, venue, symbol, funding, created_at)
                VALUES (?, 'binance', 'SUIUSDT', 0.06, 1)
                """,
                (eid,),
            )
            text = format_shock_f0(conn, eid)
            self.assertIn("High", text)

    def test_shock_not_found(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = format_shock_f0(conn, 99999)
            self.assertIn("not found", text)

    def test_entry_ai_line_from_f0_analysis(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            eid = seed_event(conn)
            conn.execute(
                """
                INSERT INTO market_event_ai_analyses_f0 (
                  event_id, prompt_version, response_json, summary_en, created_at
                ) VALUES (?, 'f0', '{}', 'Liquidity sweep detected on SUI.', 1)
                """,
                (eid,),
            )
            text = format_entry_f0(
                conn, event_id=eid, symbol="SUI", reversal_variant="R2",
                entry=1.0, stop=0.95, target=1.1,
            )
            self.assertIn("Liquidity sweep", text)


if __name__ == "__main__":
    unittest.main()
