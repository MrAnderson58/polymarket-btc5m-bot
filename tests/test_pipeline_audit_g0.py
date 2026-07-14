"""Phase G.0 — Pipeline audit tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.pipeline_audit_g0 import (
    audit_recorder_g0,
    audit_shadow_g0,
    emit_test_signal_g0,
    format_pipeline_audit_g0,
    format_recorder_debug_g0,
    format_telegram_debug_g0,
    run_pipeline_audit_g0,
)


class PipelineAuditG0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "audit.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_empty_db_audit_failures(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            stages = run_pipeline_audit_g0(conn)
            self.assertEqual(stages[0].name, "Recorder")
            self.assertEqual(stages[0].status, "FAIL")

    def test_recorder_pass_with_snapshot(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, btc_price, funding, open_interest,
                  fear_greed, collector_latency_ms, recorder_status, created_at
                ) VALUES (?, ?, 65000, 0.01, 1e9, 50, 120, 'ok', ?)
                """,
                ("t", now, now),
            )
            conn.commit()
            stage = audit_recorder_g0(conn)
            self.assertEqual(stage.status, "PASS")

    def test_format_outputs(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            text = format_pipeline_audit_g0(conn)
            self.assertIn("Recorder", text)
            self.assertIn("Shadow", text)
            self.assertIn("Current provider", format_recorder_debug_g0(conn))
            self.assertIn("API", format_recorder_debug_g0(conn))
            self.assertIn("Token OK", format_telegram_debug_g0(conn))

    def test_emit_test_signal_db_only(self) -> None:
        from bot.research.market_events.signal_intelligence import shadow_g40 as mod
        with patch.object(mod, "send_shadow_telegram_g40", return_value=True):
            with self._conn() as conn:
                apply_migrations(conn)
                now = int(time.time())
                conn.execute(
                    """
                    INSERT INTO market_snapshots_g3 (
                      snapshot_uuid, snapshot_ts, btc_price, created_at
                    ) VALUES (?, ?, 65000, ?)
                    """,
                    ("s", now, now),
                )
                conn.commit()
                result = emit_test_signal_g0(conn)
                self.assertIsNotNone(result.signal_id)
                conn.commit()
                row = conn.execute(
                    "SELECT id FROM market_shadow_signals WHERE id = ?",
                    (result.signal_id,),
                ).fetchone()
                self.assertIsNotNone(row)

    def test_shadow_audit_disabled(self) -> None:
        with patch(
            "bot.research.market_events.signal_intelligence.config.G40_SHADOW_ENABLED",
            False,
        ):
            with self._conn() as conn:
                apply_migrations(conn)
                stage = audit_shadow_g0(conn)
                self.assertEqual(stage.status, "FAIL")
                self.assertIn("ME_SHADOW_ENABLED", stage.detail)


if __name__ == "__main__":
    unittest.main()
