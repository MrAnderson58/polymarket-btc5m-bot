"""Phase FIX-3 — diagnostic CLI paths must be SQLite write-free."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.alert_engine.telegram_delivery import TelegramDeliveryResult
from bot.research.market_events.db import market_events_connection, market_events_readonly_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state, set_g3_ops_state
from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
    format_provider_status_g03,
)
from bot.research.market_events.telegram_ops.cli import run_telegram_alert_test


def _snapshot_writes(conn) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    for table in (
        "market_event_telegram_delivery_log",
        "market_decision_runs_s20",
        "market_events_migrations",
        "market_events_command_trace_g351",
    ):
        try:
            out[table] = int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        except Exception:
            out[table] = -1
    out["provider_cooldown"] = get_g3_ops_state(conn, "provider_cooldown_g03") or ""
    out["active_provider"] = get_g3_ops_state(conn, "active_provider_g03") or ""
    return out


class TestReadOnlyPurgeFix3(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(str(Path(self.tmp.name) / "me.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_provider_status_no_provider_state_write(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            set_g3_ops_state(conn, "provider_cooldown_g03", '{"bybit": 1}')
            set_g3_ops_state(conn, "active_provider_g03", "bybit")
            conn.commit()
            before = _snapshot_writes(conn)

        with market_events_readonly_connection() as conn:
            with patch(
                "bot.research.market_events.signal_intelligence.market_data_source_g01."
                "fetch_provider_market_data_g03",
                return_value=None,
            ):
                text = format_provider_status_g03(conn, symbol="BTC")

        self.assertIn("Provider Status", text)
        with market_events_readonly_connection() as conn:
            after = _snapshot_writes(conn)
        self.assertEqual(before["provider_cooldown"], after["provider_cooldown"])
        self.assertEqual(before["active_provider"], after["active_provider"])

    @patch("bot.research.market_events.alert_engine.telegram_delivery.deliver_telegram")
    def test_telegram_alert_test_passes_conn_none(self, mock_deliver) -> None:
        mock_deliver.return_value = TelegramDeliveryResult(
            ok=False, error="no_token", latency_ms=0.0,
            http_code=None, message_id=None, attempts=0, chat_id=None,
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            before = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM market_event_telegram_delivery_log"
                ).fetchone()["n"]
            )
            code, text = run_telegram_alert_test(conn)
            after = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM market_event_telegram_delivery_log"
                ).fetchone()["n"]
            )
        self.assertEqual(before, after)
        mock_deliver.assert_called_once()
        self.assertIsNone(mock_deliver.call_args.kwargs.get("conn"))
        self.assertIn("no delivery_log", text)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
