"""Regression — demo-event --force-g2 must not crash on Telegram preview."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.market_event_alerts import PAPER_LABEL
from bot.research.market_events.telegram_ops.cli import (
    _try_alert_message_text,
    run_demo_event,
)


class DemoEventForceG2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "demo_force_g2.db"
        self._env_patch = patch.dict(os.environ, {
            "ME_G2_CLAUDE_RESEARCH": "true",
            "ANTHROPIC_API_KEY": "",
        }, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_force_g2_completes_without_crash(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            code, text = run_demo_event(conn, force_g2=True)
            self.assertIn("F0→G1→F5→F7→G2 pipeline", text)
            self.assertIn("Saved to g2 table", text)
            self.assertIn(PAPER_LABEL, text)
            self.assertEqual(code, 0)

    def test_force_g2_saves_research_row(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            code, text = run_demo_event(conn, force_g2=True)
            self.assertEqual(code, 0)
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_ai_research_g2",
            ).fetchone()
            self.assertGreaterEqual(int(row["n"]), 1)
            self.assertIn("Claude called", text)

    def test_telegram_preview_optional_on_query_failure(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            with patch(
                "bot.research.market_events.telegram_ops.cli._try_alert_message_text",
                side_effect=RuntimeError("preview failed"),
            ):
                code, text = run_demo_event(conn, force_g2=True)
            self.assertEqual(code, 0)
            self.assertIn("Saved to g2 table", text)
            self.assertIn("preview unavailable", text)

    def test_force_g2_f7_trace_not_missing(self) -> None:
        from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
            STAGE_F7_COMPLETED,
            STAGE_F7_SKIPPED,
        )

        with self._conn() as conn:
            apply_migrations(conn)
            code, text = run_demo_event(conn, force_g2=True)
            self.assertEqual(code, 0)
            self.assertNotIn("F7 missing", text)
            rows = conn.execute(
                "SELECT stage FROM market_events_signal_trace_f51 ORDER BY id DESC LIMIT 20",
            ).fetchall()
            stages = {r["stage"] for r in rows}
            self.assertTrue(STAGE_F7_COMPLETED in stages or STAGE_F7_SKIPPED in stages)

    def test_alert_log_uses_message_text_column(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = 1_700_000_000
            conn.execute(
                """
                INSERT INTO market_events (
                  event_ts, detected_ts, venue, symbol, direction, phase,
                  trigger_window_seconds, return_pct, classification,
                  detector_version, detector_triggers_json, dedup_key, created_at
                ) VALUES (?, ?, 'binance_futures', 'SOL', 'DOWN', 'SHOCK_DETECTED',
                  60, -3.0, 'ASSET_SPECIFIC', 'v1', '[]', ?, ?)
                """,
                (now, now, "dedup-preview-test", now),
            )
            eid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO market_event_alert_log (
                  event_id, alert_type, dedupe_key, message_text, sent, created_at
                ) VALUES (?, 'SHOCK', ?, '🧠 Research Agent test', 1, ?)
                """,
                (eid, f"key-{eid}", now),
            )
            preview = _try_alert_message_text(conn, eid)
            self.assertIn("Research Agent", preview or "")


if __name__ == "__main__":
    unittest.main()
