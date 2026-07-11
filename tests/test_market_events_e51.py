"""Phase E.5.1 System Validation tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.system_validation.dedupe_audit import (
    check_alert_dedupe_keys,
    functional_alert_dedupe_test,
)
from bot.research.market_events.system_validation.load_test import run_synthetic_load
from bot.research.market_events.system_validation.report import format_health_report, report_to_json
from bot.research.market_events.system_validation.restart_audit import check_pending_restore
from bot.research.market_events.system_validation.runner import run_system_validation
from bot.research.market_events.system_validation.types import status_rank


class MarketEventsE51Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "me_e51.db"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _conn(self):
        return market_events_connection(db_path=self.db_path)

    def test_full_validation_passes_on_clean_db(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            results = run_system_validation(
                conn, load_events=50, benchmark_events=5,
                temp_db_path=Path(self._tmpdir.name) / "bench.db",
            )
            fails = [r for r in results if r.status == "FAIL"]
            self.assertEqual(fails, [], msg=str(fails))
            report = format_health_report(results)
            self.assertIn("VERDICT:", report)

    def test_alert_functional_dedupe(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            r = functional_alert_dedupe_test(conn)
            self.assertEqual(r.status, "PASS")

    def test_pending_restore(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            r = check_pending_restore(conn)
            self.assertEqual(r.status, "PASS")

    def test_synthetic_load_no_dedupe_violations(self) -> None:
        r = run_synthetic_load(n_events=100)
        self.assertEqual(r.metrics.get("alert_dedupe_violations"), 0)
        self.assertIn(r.status, ("PASS", "WARN"))

    def test_health_report_json(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            results = run_system_validation(conn, skip_load=True, load_events=0)
            payload = json.loads(report_to_json(results))
            self.assertEqual(payload["phase"], "E.5.1")
            self.assertIn("verdict", payload)

    def test_no_duplicate_alerts_after_seed(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_events (
                  id, event_ts, detected_ts, venue, symbol, direction, phase,
                  trigger_window_seconds, return_pct, classification,
                  detector_version, detector_triggers_json, dedup_key, created_at
                ) VALUES (1, ?, ?, 'binance_futures', 'BTC', 'DOWN', 'SHOCK_DETECTED',
                  60, -2.0, 'ASSET_SPECIFIC', 'v1', '[]', 'dedupe-e51', ?)
                """,
                (now, now, now),
            )
            conn.execute(
                """
                INSERT INTO market_event_alert_log (
                  event_id, alert_type, dedupe_key, message_text, sent, latency_ms, created_at
                ) VALUES (1, 'SHOCK_DETECTED', 'unique-key-1', 'x', 1, 1.0, ?)
                """,
                (now,),
            )
            r = check_alert_dedupe_keys(conn, days=7)
            self.assertEqual(r.status, "PASS")

    def test_status_rank_ordering(self) -> None:
        self.assertGreater(status_rank("FAIL"), status_rank("WARN"))
        self.assertGreater(status_rank("WARN"), status_rank("PASS"))


if __name__ == "__main__":
    unittest.main()
