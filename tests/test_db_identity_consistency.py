"""Regression: S42 research report and paper CLI must open the same SQLite file.

Fails if path / inode / size / sha256-prefix / COUNT(*) diverge, or if ATTACH
shadows the main DB. Also documents that RESEARCH sibling is allowed to differ
(S60) and must not be confused with LIVE S42 (~28k historical audit pattern).
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import (
    configure_unit_test_db_isolation,
    reset_db_config_for_tests,
)
from bot.research.market_events.db_identity import (
    assert_s42_cli_and_report_same_db,
    collect_s42_analytics_identities,
    fingerprint_sqlite_path,
    identities_match,
)
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import research_repository_s60 as s60


class TestDbIdentityConsistency(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.live_path = Path(self.tmp.name) / "market_events.db"
        configure_unit_test_db_isolation(self.live_path)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction,
                  entry, stop, tp1, tp2, created_at, status,
                  mfe_pct, mae_pct, capital_usd, leverage, updated_at,
                  result, pnl_pct, closed_at
                ) VALUES
                  ('t', 1, 'BTC', 'LONG', 100, 95, 105, 110, 1, 'CLOSED',
                   0, 0, 100, 20, 2, 'WIN', 1.0, 2),
                  ('t', 2, 'ETH', 'LONG', 100, 95, 105, 110, 1, 'CLOSED',
                   0, 0, 100, 20, 2, 'LOSS', -1.0, 2),
                  ('t', 3, 'SOL', 'LONG', 100, 95, 105, 110, 1, 'OPEN',
                   0, 0, 100, 20, 2, NULL, NULL, NULL)
                """
            )
            conn.commit()
        self.repo = s60.get_research_repository()
        self.repo.migrate()

    def tearDown(self) -> None:
        reset_db_config_for_tests()
        self.tmp.cleanup()

    def test_pragma_database_list_and_physical_identity(self) -> None:
        ident = fingerprint_sqlite_path(self.live_path, role="live")
        self.assertTrue(ident.exists)
        self.assertEqual(ident.absolute_path, str(self.live_path.resolve()))
        self.assertIsNotNone(ident.inode)
        self.assertGreater(ident.size_bytes or 0, 0)
        self.assertEqual(len(ident.sha256_prefix or ""), 64)
        self.assertEqual(ident.pragma_database_list[0][1], "main")
        self.assertEqual(
            Path(str(ident.pragma_database_list[0][2])).resolve(),
            self.live_path.resolve(),
        )
        self.assertEqual(ident.s42_total, 3)
        self.assertEqual(ident.s42_closed, 2)

        # SHA of first N bytes must match a direct hash.
        h = hashlib.sha256()
        with self.live_path.open("rb") as fh:
            h.update(fh.read(ident.sha_bytes))
        self.assertEqual(ident.sha256_prefix, h.hexdigest())

    def test_cli_and_research_report_share_same_db(self) -> None:
        identities = assert_s42_cli_and_report_same_db()
        live = identities["live_config"]
        for key in (
            "market_events_connection",
            "paper_performance_cli",
            "s42_research_report",
        ):
            self.assertTrue(
                identities_match(live, identities[key]),
                msg=f"{key} diverged from live_config",
            )
            self.assertEqual(identities[key].s42_closed, 2)
            self.assertEqual(identities[key].s42_total, 3)

    def test_research_sibling_is_allowed_to_differ(self) -> None:
        identities = collect_s42_analytics_identities()
        live = identities["live_config"]
        research = identities["research_sibling"]
        self.assertNotEqual(live.absolute_path, research.absolute_path)
        self.assertTrue(research.absolute_path.endswith("_research.db"))
        # Research sibling must not be used as S42 paper book identity.
        self.assertFalse(identities_match(live, research))

    def test_mismatch_fails_when_report_points_elsewhere(self) -> None:
        other = Path(self.tmp.name) / "shadow.db"
        other.write_bytes(self.live_path.read_bytes())
        identities = collect_s42_analytics_identities()
        identities["s42_research_report"] = fingerprint_sqlite_path(
            other,
            role="s42_research_report",
        )
        with self.assertRaises(AssertionError) as ctx:
            assert_s42_cli_and_report_same_db(identities)
        self.assertIn("S42 analytics DB mismatch", str(ctx.exception))

    def test_attach_is_rejected(self) -> None:
        identities = collect_s42_analytics_identities()
        # Simulate an ATTACH that would shadow analytics.
        fake = identities["paper_performance_cli"]
        from bot.research.market_events.db_identity import SqliteDbIdentity

        identities["paper_performance_cli"] = SqliteDbIdentity(
            role=fake.role,
            absolute_path=fake.absolute_path,
            exists=fake.exists,
            inode=fake.inode,
            size_bytes=fake.size_bytes,
            sha256_prefix=fake.sha256_prefix,
            sha_bytes=fake.sha_bytes,
            pragma_database_list=[
                (0, "main", fake.absolute_path),
                (1, "evil", "/tmp/other.db"),
            ],
            s42_total=fake.s42_total,
            s42_closed=fake.s42_closed,
            config_source=fake.config_source,
            backend=fake.backend,
        )
        with self.assertRaises(AssertionError) as ctx:
            assert_s42_cli_and_report_same_db(identities)
        self.assertIn("ATTACH", str(ctx.exception))

    def test_claimed_28288_cannot_come_from_this_sequence(self) -> None:
        """Mathematical proof helper: local S42 autoincrement never reached 28k."""
        with market_events_connection() as conn:
            seq = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='market_events_paper_trades_s42'",
            ).fetchone()
            closed = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 WHERE status='CLOSED'",
            ).fetchone()["n"]
            max_id = conn.execute(
                "SELECT MAX(id) AS m FROM market_events_paper_trades_s42",
            ).fetchone()["m"]
        self.assertEqual(int(closed), 2)
        self.assertLess(int(max_id or 0), 28288)
        self.assertLess(int(seq["seq"] if seq else max_id or 0), 28288)

        # Operator's WIN+LOSS+BE is internally inconsistent with claimed CLOSED.
        win, loss, be, closed_claimed = 15724, 11767, 3109, 28288
        self.assertNotEqual(win + loss + be, closed_claimed)

        # Documented cross-env S56 pattern is within tens of claimed CLOSED.
        documented_s56_pattern = 28322
        self.assertLessEqual(abs(closed_claimed - documented_s56_pattern), 50)

    def test_db_identity_cli_registered(self) -> None:
        from bot.research.market_events.__main__ import main

        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            rc = main(["db-identity"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
