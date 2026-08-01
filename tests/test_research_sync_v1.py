"""Tests for Research Sync V1 + Safety V2 activate guards."""

from __future__ import annotations

import os
import sqlite3
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.research_sync_v1 import (
    ResearchSyncActivateAborted,
    assess_activate_safety,
    auto_backup_path_for,
    collect_activate_db_stats,
    export_research_snapshot,
    format_research_sync_status,
    import_research_snapshot,
    research_sync_status,
    sha256_file,
)


def _init_minimal_schema(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events_migrations (
          version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO market_events_migrations VALUES (66, datetime('now'), 'test')"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events_trade_features_s55 (
          id INTEGER PRIMARY KEY, closed_at INTEGER, pnl_pct REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events_paper_trades_s42 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT,
          pnl_pct REAL
        )
        """
    )
    conn.commit()
    conn.close()


def _seed_closed(db: Path, n: int, *, open_n: int = 0) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM market_events_paper_trades_s42")
    for _ in range(n):
        conn.execute(
            "INSERT INTO market_events_paper_trades_s42 (status, pnl_pct) VALUES ('CLOSED', 1.0)"
        )
    for _ in range(open_n):
        conn.execute(
            "INSERT INTO market_events_paper_trades_s42 (status, pnl_pct) VALUES ('OPEN', NULL)"
        )
    conn.commit()
    conn.close()


def _pad_file(db: Path, min_size: int) -> None:
    """Grow SQLite file without changing logical row counts (freelist / vacuum filler)."""
    size = db.stat().st_size
    if size >= min_size:
        return
    # Append bytes after the SQLite header region via a disposable table + rows, then
    # leave the file large. Simpler: write padding via truncate after copy is unsafe.
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS _pad (b BLOB)")
    blob = b"x" * 64_000
    while db.stat().st_size < min_size:
        conn.execute("INSERT INTO _pad (b) VALUES (?)", (blob,))
        conn.commit()
    conn.close()


class ResearchSyncV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)
        self._db = self._root / "me.db"
        configure_unit_test_db_isolation(self._db)
        _init_minimal_schema(self._db)
        conn = sqlite3.connect(str(self._db))
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

    def _export_from(self, db: Path) -> Path:
        configure_unit_test_db_isolation(db)
        manifest = export_research_snapshot()
        return Path(manifest["archive_path"])

    def test_export_import_roundtrip_sha(self) -> None:
        _seed_closed(self._db, 2)
        manifest = export_research_snapshot()
        archive = Path(manifest["archive_path"])
        self.assertTrue(archive.exists())
        digest = manifest["db_sha256"]
        self.assertEqual(digest, sha256_file(self._db))

        # Same content activate: Safety V2 allows equal closed/size/mtime.
        result = import_research_snapshot(archive, activate=True, print_fn=lambda *_a, **_k: None)
        self.assertEqual(result["db_sha256"], digest)
        self.assertTrue(result["synced"])
        self.assertFalse(result.get("aborted"))
        self.assertFalse(result.get("rolled_back"))
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
        import_research_snapshot(archive, print_fn=lambda *_a, **_k: None)
        bad = self._root / "sync" / "market_events.db"
        bad.write_bytes(b"not-a-db")
        status = research_sync_status()
        self.assertIn(status["state"], ("INSTALL_DRIFT", "DRIFT", "SYNCED"))


class ResearchSyncSafetyV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)
        self._prod = self._root / "market_events.db"
        self._sparse = self._root / "sparse.db"
        configure_unit_test_db_isolation(self._prod)
        for p in (self._prod, self._sparse):
            _init_minimal_schema(p)

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
        for p in (
            self._sync_patcher,
            self._snap_patcher,
            self._active_patcher,
            self._installed_patcher,
        ):
            p.start()

    def tearDown(self) -> None:
        for p in (
            self._installed_patcher,
            self._active_patcher,
            self._snap_patcher,
            self._sync_patcher,
        ):
            p.stop()
        self._tmpdir.cleanup()
        os.environ.pop("MARKET_EVENTS_DATABASE_PATH", None)
        os.environ.pop("MARKET_EVENTS_DB_URL", None)
        os.environ.pop("MARKET_EVENTS_DB_BACKEND", None)

    def _archive_from(self, db: Path) -> Path:
        configure_unit_test_db_isolation(db)
        return Path(export_research_snapshot()["archive_path"])

    def _make_regressive_snapshot(self) -> Path:
        """Production rich/new/large; snapshot sparse/old/small."""
        _seed_closed(self._sparse, 3)
        archive = self._archive_from(self._sparse)
        # Production becomes richer AFTER snapshot export.
        configure_unit_test_db_isolation(self._prod)
        _seed_closed(self._prod, 12, open_n=2)
        _pad_file(self._prod, min_size=400_000)
        time.sleep(0.05)
        os.utime(self._prod, None)
        return archive

    def test_abort_snapshot_fewer_trades(self) -> None:
        archive = self._make_regressive_snapshot()
        with self.assertRaises(ResearchSyncActivateAborted) as ctx:
            import_research_snapshot(archive, activate=True, print_fn=lambda *_a, **_k: None)
        self.assertIn("fewer CLOSED", str(ctx.exception))
        self.assertEqual(collect_activate_db_stats(self._prod)["closed"], 12)

    def test_abort_snapshot_smaller(self) -> None:
        _seed_closed(self._sparse, 5)
        archive = self._archive_from(self._sparse)
        configure_unit_test_db_isolation(self._prod)
        _seed_closed(self._prod, 5)
        _pad_file(self._prod, min_size=500_000)
        # Equal closed; size regresses.
        with self.assertRaises(ResearchSyncActivateAborted) as ctx:
            import_research_snapshot(archive, activate=True, print_fn=lambda *_a, **_k: None)
        self.assertIn("smaller", str(ctx.exception))

    def test_abort_snapshot_older(self) -> None:
        _seed_closed(self._sparse, 5)
        # Freeze sparse mtime in the past via archive contents; production newer.
        archive = self._archive_from(self._sparse)
        configure_unit_test_db_isolation(self._prod)
        _seed_closed(self._prod, 5)
        # Ensure production mtime is strictly newer than installed snapshot after import.
        # Touch production after a short delay; import will install snapshot with older mtime
        # (copy2 preserves sparse mtime from archive).
        time.sleep(0.05)
        os.utime(self._prod, (time.time() + 10, time.time() + 10))
        with self.assertRaises(ResearchSyncActivateAborted) as ctx:
            import_research_snapshot(archive, activate=True, print_fn=lambda *_a, **_k: None)
        msg = str(ctx.exception)
        self.assertTrue("older" in msg or "smaller" in msg or "fewer" in msg)

    def test_successful_activate(self) -> None:
        # Snapshot better than production.
        _seed_closed(self._prod, 2)
        configure_unit_test_db_isolation(self._sparse)
        _seed_closed(self._sparse, 8)
        _pad_file(self._sparse, min_size=300_000)
        time.sleep(0.02)
        os.utime(self._sparse, None)
        archive = self._archive_from(self._sparse)
        configure_unit_test_db_isolation(self._prod)
        # production older/smaller/fewer
        os.utime(self._prod, (1_700_000_000, 1_700_000_000))
        result = import_research_snapshot(archive, activate=True, print_fn=lambda *_a, **_k: None)
        self.assertTrue(result.get("activated"))
        self.assertFalse(result.get("aborted"))
        self.assertFalse(result.get("rolled_back"))
        self.assertTrue(result.get("auto_backup"))
        self.assertEqual(collect_activate_db_stats(self._prod)["closed"], 8)
        self.assertTrue(Path(result["auto_backup"]).exists())

    def test_rollback_when_closed_decreases_under_force(self) -> None:
        archive = self._make_regressive_snapshot()
        before = collect_activate_db_stats(self._prod)
        self.assertEqual(before["closed"], 12)
        result = import_research_snapshot(
            archive,
            activate=True,
            force=True,
            print_fn=lambda *_a, **_k: None,
        )
        self.assertTrue(result.get("force"))
        self.assertTrue(result.get("rolled_back"))
        self.assertTrue(result.get("auto_backup"))
        after = collect_activate_db_stats(self._prod)
        self.assertEqual(after["closed"], 12)
        self.assertEqual(after["sha"], before["sha"])

    def test_force_activate_bypasses_older_smaller_same_closed(self) -> None:
        _seed_closed(self._sparse, 4)
        archive = self._archive_from(self._sparse)
        configure_unit_test_db_isolation(self._prod)
        _seed_closed(self._prod, 4)
        _pad_file(self._prod, min_size=450_000)
        os.utime(self._prod, (time.time() + 20, time.time() + 20))
        with self.assertRaises(ResearchSyncActivateAborted):
            import_research_snapshot(archive, activate=True, print_fn=lambda *_a, **_k: None)
        result = import_research_snapshot(
            archive,
            activate=True,
            force=True,
            print_fn=lambda *_a, **_k: None,
        )
        self.assertFalse(result.get("aborted"))
        self.assertFalse(result.get("rolled_back"))  # CLOSED unchanged
        self.assertEqual(collect_activate_db_stats(self._prod)["closed"], 4)
        self.assertLess(collect_activate_db_stats(self._prod)["size"], 450_000)

    def test_assess_and_backup_helpers(self) -> None:
        prod = {"exists": True, "closed": 10, "mtime": 100.0, "size": 1000}
        snap = {"exists": True, "closed": 3, "mtime": 50.0, "size": 100}
        reasons = assess_activate_safety(prod, snap)
        self.assertTrue(any("production.closed" in r for r in reasons))
        self.assertTrue(any("fewer CLOSED" in r for r in reasons))
        self.assertTrue(any("older" in r for r in reasons))
        self.assertTrue(any("smaller" in r for r in reasons))
        bak = auto_backup_path_for(Path("/tmp/market_events.db"), stamp="20260101_120000")
        self.assertEqual(bak.name, "market_events.db.auto_backup.20260101_120000")


if __name__ == "__main__":
    unittest.main()
