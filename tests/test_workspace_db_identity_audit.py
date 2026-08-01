"""Tests for Workspace DB Identity Audit."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.workspace_db_identity_audit import (
    resolve_market_math_db_path,
    run_workspace_db_identity_audit,
    terminal_default_db_path,
)


class TestWorkspaceDbIdentityAudit(unittest.TestCase):
    def test_terminal_default_under_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = terminal_default_db_path(cwd=td)
            self.assertEqual(p, (Path(td) / "data" / "market_events.db").resolve())

    def test_audit_text_contains_required_sections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "data" / "market_events.db"
            db.parent.mkdir(parents=True)
            con = sqlite3.connect(str(db))
            con.execute(
                "CREATE TABLE market_events_paper_trades_s42 ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, "
                "pnl_usd REAL, pnl_pct REAL)"
            )
            con.execute(
                "INSERT INTO market_events_paper_trades_s42 (status, pnl_usd, pnl_pct) "
                "VALUES ('CLOSED', 1.5, 2.0)"
            )
            con.commit()
            con.close()

            with mock.patch(
                "bot.research.market_events.workspace_db_identity_audit.resolve_research_analytics_sqlite_path",
                return_value=(db.resolve(), "test", object()),
            ), mock.patch(
                "bot.research.market_events.workspace_db_identity_audit.find_market_events_dbs",
                return_value=[],
            ), mock.patch(
                "bot.research.market_events.workspace_db_identity_audit.os.getcwd",
                return_value=td,
            ):
                text = run_workspace_db_identity_audit(search_disk=True, cwd=td)

        self.assertIn("os.getcwd():", text)
        self.assertIn("git rev-parse --show-toplevel:", text)
        self.assertIn("pwd:", text)
        self.assertIn("resolved_db_path:", text)
        self.assertIn("os.stat(path).st_ino:", text)
        self.assertIn("sha256(first 16MB):", text)
        self.assertIn("PRAGMA database_list;", text)
        self.assertIn("sqlite_sequence for market_events_paper_trades_s42:", text)
        self.assertIn("Cursor analyzed:", text)
        self.assertIn("Terminal analyzed:", text)
        self.assertIn("Same DB:", text)
        self.assertIn(str(db.resolve()), text)
        self.assertIn("YES", text)

    def test_cli_registered(self) -> None:
        from bot.research.market_events.__main__ import main

        with mock.patch(
            "bot.research.market_events.workspace_db_identity_audit.run_workspace_db_identity_audit",
            return_value="ok",
        ), mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            rc = main(["workspace-db-identity-audit"])
        self.assertEqual(rc, 0)

    def test_market_math_resolver_callable(self) -> None:
        path, source = resolve_market_math_db_path()
        self.assertTrue(str(path))
        self.assertTrue(source)


if __name__ == "__main__":
    unittest.main()
