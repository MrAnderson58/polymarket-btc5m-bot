"""S60.1 CLI architecture audit tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.signal_intelligence import cli_architecture_audit_s601 as s601
from tests.research_db_helpers import ensure_research_schema


class TestCliArchitectureAuditS601(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s601.db")
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extractors_find_recent_commands(self) -> None:
        root = s601._repo_root()
        source = s601._main_py(root).read_text(encoding="utf-8")
        registered = s601.extract_registered_commands(source)
        dispatched = s601.extract_dispatched_commands(source)
        self.assertIn("feature-lab", registered)
        self.assertIn("strategy-discovery", registered)
        self.assertIn("market-research-migrate", registered)
        self.assertIn("cli-architecture-audit", registered)
        self.assertIn("feature-lab", dispatched)
        self.assertIn("cli-architecture-audit", dispatched)
        self.assertFalse(set(registered) - dispatched)

    def test_audit_report(self) -> None:
        audit = s601.run_cli_architecture_audit()
        self.assertTrue(audit.implemented)
        self.assertIn("feature-lab", audit.implemented)
        self.assertIn("decision-report", audit.implemented)
        self.assertIn("market-regime", audit.implemented)
        self.assertIn("strategy-discovery", audit.implemented)
        self.assertIn("market-research-migrate", audit.implemented)
        self.assertEqual(audit.registered_without_dispatch, [])
        text = s601.format_cli_architecture_audit(audit)
        self.assertIn("S60.1 CLI & Architecture Audit", text)
        self.assertIn("✓ S59 feature-lab", text)
        self.assertIn("✓ S61 strategy-discovery", text)
        self.assertIn("✓ S60 market-research-migrate", text)
        self.assertIn("Migrations:", text)
        self.assertGreaterEqual(audit.migrations["code"]["RESEARCH_SCHEMA_VERSION"], 71)
        self.assertGreaterEqual(audit.migrations["code"]["LIVE_SCHEMA_VERSION"], 65)


if __name__ == "__main__":
    unittest.main()
