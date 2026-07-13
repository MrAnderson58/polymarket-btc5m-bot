"""Phase F.6 — trader performance learning tests."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.trader_performance_f6 import (
    TraderPerformanceF6,
    apply_author_confidence,
    author_confidence_bonus,
    format_author_telegram_block,
    load_trader_performance,
    record_paper_close_f6,
    resolve_channel_for_event,
    trader_performance_report,
    trader_ranking_report,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_event


class TraderPerformanceF6Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {
            "ME_F6_TRADER_PERFORMANCE": "true",
            "ME_F6_MIN_SIGNALS": "3",
        }, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v20(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v22", applied)
            self.assertEqual(SCHEMA_VERSION, 34)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_trader_performance_f6", tables)

    def test_author_bonus_mapping(self) -> None:
        self.assertAlmostEqual(author_confidence_bonus(0.82, signals_count=10), 0.7, places=1)
        self.assertAlmostEqual(author_confidence_bonus(0.38, signals_count=10), -1.0, places=1)
        self.assertEqual(author_confidence_bonus(0.82, signals_count=2), 0.0)

    def test_apply_author_confidence(self) -> None:
        perf = TraderPerformanceF6(
            channel_name="signalyp",
            signals_count=20,
            wins=16,
            losses=4,
            win_rate=0.82,
            avg_rr=2.9,
            avg_pnl=1.2,
            max_drawdown=3.0,
            avg_tp_time_sec=3600,
            author_score=0.7,
            last_30_wins=24,
            last_30_losses=6,
            sufficient=True,
        )
        self.assertAlmostEqual(apply_author_confidence(6.5, perf), 7.2, places=1)

    def test_record_and_load_performance(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_event_context (
                  event_id, context_type, source, source_record_id,
                  context_ts, time_delta_seconds, relevance_score, context_json, created_at
                ) VALUES (?, 'TELEGRAM_SIGNAL', 'signalyp', '1', ?, 0, 0.9, '{}', ?)
                """,
                (event_id, now, now),
            )
            conn.commit()

            for i, ret in enumerate([2.5, -1.0, 3.0, 1.5]):
                record_paper_close_f6(
                    conn,
                    event_id=event_id,
                    net_return=ret,
                    exit_reason="TP" if ret > 0 else "STOP",
                    duration_seconds=1800 + i * 100,
                    mfe=abs(ret) + 1,
                    mae=-1.5,
                    gross_return=ret,
                    reversal_variant="R1",
                    exit_variant=f"E{i}",
                )
            conn.commit()

            perf = load_trader_performance(conn, "signalyp")
            self.assertIsNotNone(perf)
            assert perf is not None
            self.assertEqual(perf.signals_count, 4)
            self.assertEqual(perf.wins, 3)
            self.assertEqual(perf.losses, 1)
            self.assertAlmostEqual(perf.win_rate, 0.75, places=2)

            text = trader_performance_report(conn, channel="signalyp")
            self.assertIn("signalyp", text)
            self.assertIn("win rate", text.lower())

            ranking = trader_ranking_report(conn)
            self.assertIn("TRADER RANKING", ranking)

    def test_telegram_block_new_author(self) -> None:
        lines = format_author_telegram_block(None, channel="signalyp")
        text = "\n".join(lines)
        self.assertIn("Автор новый", text)
        self.assertIn("Статистика недостаточна", text)

    def test_telegram_block_experienced_author(self) -> None:
        perf = TraderPerformanceF6(
            channel_name="signalyp",
            signals_count=30,
            wins=24,
            losses=6,
            win_rate=0.81,
            avg_rr=2.9,
            avg_pnl=1.0,
            max_drawdown=4.0,
            avg_tp_time_sec=2000,
            author_score=0.65,
            last_30_wins=24,
            last_30_losses=6,
            sufficient=True,
        )
        text = "\n".join(format_author_telegram_block(perf, channel="signalyp"))
        self.assertIn("Win Rate: 81%", text)
        self.assertIn("Средний RR: 2.9", text)
        self.assertIn("24 успешных", text)
        self.assertIn("6 неудачных", text)

    def test_resolve_channel(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_event_context (
                  event_id, context_type, source, source_record_id,
                  context_ts, time_delta_seconds, relevance_score, context_json, created_at
                ) VALUES (?, 'TRADER_THESIS', 'alpha_channel', '9', ?, 0, 0.5, '{}', ?)
                """,
                (event_id, now, now),
            )
            conn.commit()
            self.assertEqual(resolve_channel_for_event(conn, event_id), "alpha_channel")


if __name__ == "__main__":
    unittest.main()
