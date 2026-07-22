"""S53 — Paper Trading audit & schema alignment tests."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from bot.research.ai_analyst.signal_consistency.repository import (
    COMMAND_DATA_SOURCES,
    SignalTruthRepository,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
    format_trading_audit_report,
)


class TestTradingAuditS53(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s53.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = int(__import__("time").time())
            conn.execute(
                """
                INSERT OR REPLACE INTO market_events_paper_account_s42
                  (id, initial_capital, current_equity, updated_at)
                VALUES (1, 100, 55682.62, ?)
                """,
                (now,),
            )
            for i in range(3):
                conn.execute(
                    """
                    INSERT INTO market_events_paper_trades_s42 (
                      s40_signal_type, s40_signal_id, symbol, direction,
                      entry, stop, tp1, tp2, created_at, status,
                      capital_usd, leverage, updated_at
                    ) VALUES ('audit', ?, 'BTC', 'LONG', 1, 0.9, 1.1, 1.2, ?, 'OPEN', 100, 20, ?)
                    """,
                    (i, now, now),
                )
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, closed_at, status,
                  result, pnl_usd, capital_usd, leverage, updated_at
                ) VALUES ('audit', 100, 'ETH', 'SHORT', 1, 1.1, 0.9, 0.8, ?, ?, 'CLOSED',
                  'WIN', 12.5, 100, 20, ?)
                """,
                (now - 200, now - 10, now),
            )
            conn.commit()

        @contextmanager
        def factory():
            with market_events_connection() as c:
                yield c

        self.repo = SignalTruthRepository(conn_factory=factory)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_audit_report_shape(self) -> None:
        with patch(
            "bot.research.ai_analyst.signal_consistency.repository.get_repository",
            return_value=self.repo,
        ):
            with market_events_connection() as conn:
                text = format_trading_audit_report(conn)
        self.assertIn("Paper Trading Audit", text)
        self.assertIn("market_events_paper_trades_s42", text)
        self.assertIn("Open:\n3", text)
        self.assertIn("Closed:\n1", text)
        self.assertIn("Synced ✓", text)
        self.assertIn("/signals            →  ai_signal_history_s48", text)
        self.assertIn("paper-performance  →  market_events_paper_trades_s42", text)

    def test_source_map_complete(self) -> None:
        for key in (
            "paper-performance",
            "trading-audit",
            "doctor.open_trades",
            "/open",
            "/stats",
            "/signals",
        ):
            self.assertIn(key, COMMAND_DATA_SOURCES)

    def test_cli_registered(self) -> None:
        from bot.research.market_events.__main__ import main
        import sys

        buf = StringIO()
        with patch.object(sys, "argv", ["market_events", "--help"]), patch("sys.stdout", buf):
            try:
                main(["--help"])
            except SystemExit:
                pass
        help_text = buf.getvalue()
        self.assertIn("trading-audit", help_text)
        self.assertIn("db-info", help_text)

    def test_open_stats_aligned_on_s42(self) -> None:
        self.assertEqual(self.repo.count_paper_open_trades(), 3)
        self.assertEqual(self.repo.count_open_trades(), 3)
        dash = self.repo.paper_stats_dashboard()
        self.assertEqual(dash["open_trades"], 3)
        self.assertEqual(dash["closed_trades"], 1)


class TestDbInfoSourcesS53(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "dbinfo.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_db_info_includes_command_map(self) -> None:
        from bot.research.market_events.db_tools import format_db_info

        text = format_db_info()
        self.assertIn("Command → data source", text)
        self.assertIn("paper-performance", text)
        self.assertIn("Active trading tables", text)
        self.assertIn("market_events_paper_trades_s42", text)


if __name__ == "__main__":
    unittest.main()
