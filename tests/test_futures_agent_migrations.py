"""Regression tests for futures_agent migrations (Stage 4, PostgreSQL semantics)."""

from __future__ import annotations

import unittest

from bot.research.futures_agent import schema


class FuturesAgentMigrationSqlTestCase(unittest.TestCase):
    def test_stage4_postgres_ddl_has_no_do_blocks(self) -> None:
        ddl = schema.STAGE4_DDL_POSTGRES
        # Regression: Stage 4 must not rely on DO $$ blocks that are split incorrectly.
        self.assertNotIn("DO $$", ddl)
        parts = schema._split_ddl(ddl)
        # Ensure split parts are non-empty PostgreSQL statements (no dollar-quote fragments).
        self.assertGreaterEqual(len(parts), 2)
        for stmt in parts:
            self.assertNotIn("$$", stmt)


if __name__ == "__main__":
    unittest.main()

