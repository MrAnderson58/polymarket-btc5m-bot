"""Tests for Research Sync V1."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.research_sync_v1 import (
    ACTIVE_MANIFEST,
    INSTALLED_DB,
    RESEARCH_SYNC_DIR,
    export_research_snapshot,
    format_research_sync_status,
    import_research_snapshot,
    research_sync_status,
    sha256_file,
)


class ResearchSyncV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)
        self._db = self._root / "me.db"
        configure_unit_test_db_isolation(self._db)
        conn = sqlite3.connect(str(self._db))
        conn.execute(
            """
            CREATE TABLE market_events_migrations (
              version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO market_events_migrations VALUES (66, datetime('now'), 'test')"
        )
        conn.execute(
            """
            CREATE TABLE market_events_trade_features_s55 (
              id INTEGER PRIMARY KEY, closed_at INTEGER, pnl_pct REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO market_events_trade_features_s55 (closed_at, pnl_pct) VALUES (1, 1.0)"
        )
        conn.commit()
        conn.close()

        self._sync_patcher = mock.patch(
            "bot.research.market_events.research_sync_v1.RESEARCH_SYNC_DIR",
            self._root / "sync",
        )
        self._snap_patcher = mock.patch(
            "bot.research.market_events.research_sync_v1.SNAPSHOTS_DIR",
            self._root / "snapshots",
        )
        self._active_patcher = mock.patch(
            "bot.research.market_events.research_sync_v1.ACTIVE_MANIFEST",
            self._root / "sync" / "active_manifest.json",
        )
        self._installed_patcher = mock.patch(
            "bot.research.market_events.research_sync_v1.INSTALLED_DB",
            self._root / "sync" / "market_events.db",
        )
        self._sync_patcher.start()
        self._snap_patcher.start()
        self._active_patcher.start()
        self._installed_patcher.start()

    def tearDown(self) -> None:
        self._installed_patcher.stop()
        self._active_patcher.stop()
        self._snap_patcher.stop()
        self._sync_patcher.stop()
        self._tmpdir.cleanup()
        os.environ.pop("MARKET_EVENTS_DATABASE_PATH", None)
        os.environ.pop("MARKET_EVENTS_DB_URL", None)
        os.environ.pop("MARKET_EVENTS_DB_BACKEND", None)

    def test_export_import_roundtrip_sha(self) -> None:
        manifest = export_research_snapshot()
        archive = Path(manifest["archive_path"])
        self.assertTrue(archive.exists())
        digest = manifest["db_sha256"]
        self.assertEqual(digest, sha256_file(self._db))

        result = import_research_snapshot(archive, activate=True)
        self.assertEqual(result["db_sha256"], digest)
        self.assertTrue(result["synced"])
        self.assertTrue((self._root / "sync" / "market_events.db").exists())

        status = research_sync_status()
        self.assertEqual(status["state"], "SYNCED")
        self.assertTrue(status["sha_match"])
        text = format_research_sync_status()
        self.assertIn("RESEARCH SYNC STATUS", text)

    def test_doctor_lists_export_db(self) -> None:
        from bot.research.market_events.research_sync_v1 import (
            build_research_sync_doctor,
            format_research_sync_doctor,
        )

        doc = build_research_sync_doctor()
        self.assertTrue(doc.get("export_db_path"))
        text = format_research_sync_doctor()
        self.assertIn("RESEARCH SYNC DOCTOR", text)
        self.assertIn("EXPORT", text)
        manifest = export_research_snapshot()
        archive = Path(manifest["archive_path"])
        import_research_snapshot(archive)
        # corrupt installed copy
        bad = self._root / "sync" / "market_events.db"
        bad.write_bytes(b"not-a-db")
        status = research_sync_status()
        self.assertIn(status["state"], ("INSTALL_DRIFT", "DRIFT", "SYNCED"))


if __name__ == "__main__":
    unittest.main()
