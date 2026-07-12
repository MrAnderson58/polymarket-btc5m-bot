"""Phase F.5.1 — signal pipeline trace tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.hooks import on_shock_f0
from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
    STAGE_CONFIDENCE,
    STAGE_PARSER,
    STAGE_RAW,
    STAGE_TELEGRAM,
    STAGE_TELEGRAM_FILTER,
    fetch_trace_rows,
    format_signal_trace,
    record_f5_delivery_trace,
    record_parser_validation_snapshot,
    record_shock_received,
    record_trace,
)
from bot.research.market_events.alert_engine.timeline import build_event_timeline
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event


class SignalTraceF51Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()
        self._patch = patch.dict(os.environ, {
            "ME_F0_SIGNAL_INTELLIGENCE": "true",
            "ME_F1_TELEGRAM_INTELLIGENCE": "true",
            "ME_F2_PROFESSIONAL_INTEL": "true",
            "ME_F5_PROFESSIONAL_SIGNAL": "true",
            "ME_F5_MIN_TELEGRAM_CONFIDENCE": "7.0",
            "ME_F5_PRIORITY_ENGINE": "true",
            "ME_F5_TOP_N_TELEGRAM": "3",
            "ME_TREND_SHOCK_ENABLED": "false",
            "ME_F4_TREND_SHOCK_V2": "false",
            "ME_F4_VISUAL_INTEL": "false",
            "ME_F0_AI_ENABLED": "false",
        }, clear=False)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def test_schema_v19(self) -> None:
        with conn_ctx(self.db) as conn:
            applied = apply_migrations(conn)
            self.assertIn("v21", applied)
            self.assertEqual(SCHEMA_VERSION, 21)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_events_signal_trace_f51", tables)

    def test_record_and_format_low_confidence_trace(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn, symbol="MANTA")
            record_shock_received(conn, event_id=event_id)
            record_parser_validation_snapshot(
                conn, event_id=event_id, symbol="MANTA", snapshot_ok=True,
            )
            for stage in ("F1", "F2", "F3", "F4", "F5"):
                record_trace(conn, event_id=event_id, stage=stage, status="PASS")
            record_f5_delivery_trace(
                conn,
                event_id=event_id,
                message_id=245,
                dynamic_confidence=6.8,
                telegram_eligible=False,
                telegram_skip_reason="low_confidence",
                telegram_sent=False,
            )

            text = format_signal_trace(conn, event_id=event_id)
            self.assertIn("Message 245", text)
            self.assertIn("symbol=MANTA", text)
            self.assertIn("direction=DOWN", text)
            self.assertIn("6.8", text)
            self.assertIn("SKIPPED", text)
            self.assertIn("confidence below threshold (7.0)", text)
            self.assertIn("not sent", text)
            self.assertIn("stored", text)

            rows = fetch_trace_rows(conn, event_id=event_id)
            stages = [r["stage"] for r in rows]
            self.assertIn(STAGE_RAW, stages)
            self.assertIn(STAGE_PARSER, stages)
            self.assertIn(STAGE_CONFIDENCE, stages)
            self.assertIn(STAGE_TELEGRAM_FILTER, stages)
            self.assertIn(STAGE_TELEGRAM, stages)

    def test_parser_failure_format(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn, symbol="")
            record_trace(
                conn,
                event_id=event_id,
                stage=STAGE_PARSER,
                status="FAILED",
                reason="unknown symbol",
            )
            text = format_signal_trace(conn, event_id=event_id)
            self.assertIn("Parser", text)
            self.assertIn("FAILED", text)
            self.assertIn("unknown symbol", text)

    def test_timeline_includes_trace(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn, symbol="BTC")
            record_shock_received(conn, event_id=event_id)
            record_trace(conn, event_id=event_id, stage=STAGE_RAW, status="PASS", reason="received")

            timeline = build_event_timeline(conn, event_id=event_id)
            trace_steps = [s for s in timeline if s.get("source") == "signal_trace_f51"]
            self.assertTrue(trace_steps)
            self.assertEqual(trace_steps[0]["stage"], STAGE_RAW)

    def test_hooks_record_intelligence_stages(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn, symbol="BTC")
            seed_candles(conn, symbol="BTC")
            conn.execute(
                """
                INSERT INTO market_event_snapshots (
                  event_id, snapshot_ts, offset_seconds, price, volume
                ) VALUES (?, ?, 0, 50000, 1000)
                """,
                (event_id, 1_700_000_000),
            )
            conn.commit()

            with patch(
                "bot.research.market_events.signal_intelligence.alert_f5.send_professional_alert_f5",
                return_value=False,
            ):
                on_shock_f0(conn, event_id=event_id)

            rows = fetch_trace_rows(conn, event_id=event_id)
            stages = {r["stage"] for r in rows}
            self.assertIn("F1", stages)
            self.assertIn("F2", stages)
            self.assertIn(STAGE_PARSER, stages)

    def test_snapshot_failure_format(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            event_id = seed_event(conn, symbol="FAKECOIN")
            record_parser_validation_snapshot(
                conn, event_id=event_id, symbol="FAKECOIN", snapshot_ok=False,
            )
            text = format_signal_trace(conn, event_id=event_id)
            self.assertIn("Snapshot", text)
            self.assertIn("symbol not found on exchange", text)


if __name__ == "__main__":
    unittest.main()
