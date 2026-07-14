"""Phase G.0.4 — Telegram inbound repair tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import (
    market_events_connection,
    market_events_readonly_connection,
)
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.telegram_inbound_g04 import (
    diagnose_signal_g04,
    format_telegram_self_test_g04,
    normalize_inbound_text_g04,
    simulate_telegram_message_g04,
)
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    handle_market_events_command,
)


class TelegramInboundG04Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g04.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_sxt_short_with_levels_is_signal(self) -> None:
        text = "$SXT шорт\nEntry 0.00907\nTP 0.00896\nSL 0.00967"
        diag = diagnose_signal_g04(text)
        self.assertEqual(diag.label, "SIGNAL")
        self.assertEqual(diag.symbol, "SXT")
        self.assertEqual(diag.side, "SHORT")
        self.assertEqual(diag.taxonomy, "EXPLICIT_SIGNAL")

    def test_bare_sxt_short_reports_missing_levels(self) -> None:
        diag = diagnose_signal_g04("$SXT шорт")
        self.assertIn("Missing Entry", diag.missing)
        self.assertIn("Missing TP", diag.missing)
        self.assertNotEqual(diag.label, "SIGNAL")

    def test_normalize_lone_dollar_line(self) -> None:
        raw = "$\nSXT\nSHORT\nEntry 0.00907\nTP 0.00896\nSL 0.00967"
        norm = normalize_inbound_text_g04(raw)
        self.assertTrue(norm.startswith("$SXT") or "$SXT" in norm.replace("\n", ""))
        diag = diagnose_signal_g04(raw)
        self.assertEqual(diag.label, "SIGNAL")

    def test_readonly_connection_does_not_write(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        with market_events_readonly_connection() as ro:
            row = ro.execute("SELECT COUNT(*) AS n FROM market_snapshots_g3").fetchone()
            self.assertIsNotNone(row)
            with self.assertRaises(Exception):
                ro.execute(
                    "INSERT INTO market_events_command_trace_g351 "
                    "(message_id, command, stage, status, created_at) "
                    "VALUES (1, '/x', 't', 'PASS', 1)",
                )

    def test_status_uses_readonly_path(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        with patch(
            "bot.research.market_events.db.market_events_readonly_connection",
            wraps=market_events_readonly_connection,
        ) as mock_ro:
            result = handle_market_events_command("/status")
        self.assertTrue(result.ok)
        self.assertTrue(mock_ro.called)

    def test_simulate_and_self_test(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        out = simulate_telegram_message_g04(
            "$SXT шорт\nEntry 0.00907\nTP 0.00896\nSL 0.00967",
        )
        self.assertIn("SIGNAL", out)
        self.assertIn("SXT", out)
        report = format_telegram_self_test_g04()
        self.assertIn("parser", report)
        self.assertIn("PASS", report)


if __name__ == "__main__":
    unittest.main()
