"""Research Sync V1 — portable SQLite snapshots for reproducible analytics."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db_config import resolve_market_events_db_config
from bot.research.market_events.event_schema import SCHEMA_VERSION

MANIFEST_VERSION = 1
RESEARCH_SYNC_DIR = BASE_DIR / "data" / "research_sync"
SNAPSHOTS_DIR = BASE_DIR / "data" / "research_snapshots"
ACTIVE_MANIFEST = RESEARCH_SYNC_DIR / "active_manifest.json"
INSTALLED_DB = RESEARCH_SYNC_DIR / "market_events.db"


class ResearchSyncError(Exception):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _checkpoint_sqlite(path: Path) -> None:
    if not path.exists():
        return
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.commit()
    finally:
        conn.close()


def _snapshot_counts(db_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not db_path.exists():
        return counts
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        for table, sql in (
            ("s55_closed", "SELECT COUNT(*) AS n FROM market_events_trade_features_s55 WHERE closed_at IS NOT NULL"),
            ("s55_all", "SELECT COUNT(*) AS n FROM market_events_trade_features_s55"),
            ("paper_trades_s42", "SELECT COUNT(*) AS n FROM paper_trades_s42"),
        ):
            try:
                row = conn.execute(sql).fetchone()
                counts[table] = int(row["n"] if row else 0)
            except sqlite3.Error:
                counts[table] = 0
        try:
            row = conn.execute("SELECT MAX(version) AS v FROM market_events_migrations").fetchone()
            counts["schema_migration"] = int(row["v"] or 0)
        except sqlite3.Error:
            counts["schema_migration"] = 0
    finally:
        conn.close()
    return counts


def build_manifest(db_path: Path, *, snapshot_id: str, created_at: str) -> dict[str, Any]:
    digest = sha256_file(db_path)
    counts = _snapshot_counts(db_path)
    return {
        "manifest_version": MANIFEST_VERSION,
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "db_filename": "market_events.db",
        "db_sha256": digest,
        "schema_watermark": SCHEMA_VERSION,
        "schema_migration": counts.get("schema_migration", 0),
        "counts": counts,
    }


def verify_manifest(manifest: dict[str, Any], db_path: Path) -> None:
    expected = str(manifest.get("db_sha256") or "")
    if not expected:
        raise ResearchSyncError("manifest missing db_sha256")
    actual = sha256_file(db_path)
    if actual != expected:
        raise ResearchSyncError(
            f"SHA256 mismatch: expected {expected[:16]}… got {actual[:16]}…",
        )


def export_research_snapshot(*, dest: Path | None = None) -> dict[str, Any]:
    cfg = resolve_market_events_db_config()
    if cfg.backend != "sqlite":
        raise ResearchSyncError(
            "research-sync-export supports SQLite only in V1 (set MARKET_EVENTS_DB_BACKEND=sqlite)",
        )
    src = cfg.sqlite_path
    if not src.exists():
        raise ResearchSyncError(f"source database not found: {src}")

    _checkpoint_sqlite(src)
    created_at = _utc_now_iso()
    snapshot_id = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    archive = dest or (SNAPSHOTS_DIR / f"research_snapshot_{snapshot_id}.tar.gz")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        db_copy = tmpdir / "market_events.db"
        shutil.copy2(src, db_copy)
        manifest = build_manifest(db_copy, snapshot_id=snapshot_id, created_at=created_at)
        manifest_path = tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(db_copy, arcname="market_events.db")
            tar.add(manifest_path, arcname="manifest.json")

    manifest["archive_path"] = str(archive)
    manifest["source_path"] = str(src)
    return manifest


def import_research_snapshot(
    archive: Path,
    *,
    activate: bool = False,
) -> dict[str, Any]:
    if not archive.exists():
        raise ResearchSyncError(f"snapshot not found: {archive}")

    RESEARCH_SYNC_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmpdir)
        db_path = tmpdir / "market_events.db"
        manifest_path = tmpdir / "manifest.json"
        if not db_path.exists() or not manifest_path.exists():
            raise ResearchSyncError("invalid snapshot archive (need market_events.db + manifest.json)")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify_manifest(manifest, db_path)

        if INSTALLED_DB.exists():
            backup = RESEARCH_SYNC_DIR / f"market_events.db.bak.{int(time.time())}"
            shutil.copy2(INSTALLED_DB, backup)
        shutil.copy2(db_path, INSTALLED_DB)
        manifest["installed_at"] = _utc_now_iso()
        manifest["installed_from"] = str(archive)
        ACTIVE_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = dict(manifest)
    cfg = resolve_market_events_db_config()
    result["analytics_db_path"] = str(cfg.sqlite_path)
    result["sync_db_path"] = str(INSTALLED_DB)
    result["synced"] = sha256_file(cfg.sqlite_path) == manifest["db_sha256"] if cfg.sqlite_path.exists() else False

    if activate:
        if cfg.backend != "sqlite":
            raise ResearchSyncError("--activate requires SQLite backend")
        _checkpoint_sqlite(cfg.sqlite_path)
        if cfg.sqlite_path.exists() and cfg.sqlite_path.resolve() != INSTALLED_DB.resolve():
            bak = cfg.sqlite_path.with_suffix(f".db.pre_sync_{int(time.time())}")
            shutil.copy2(cfg.sqlite_path, bak)
            result["replaced_backup"] = str(bak)
        shutil.copy2(INSTALLED_DB, cfg.sqlite_path)
        result["activated_path"] = str(cfg.sqlite_path)
        result["synced"] = True

    return result


def research_sync_status() -> dict[str, Any]:
    cfg = resolve_market_events_db_config()
    out: dict[str, Any] = {
        "backend": cfg.backend,
        "analytics_db_path": str(cfg.sqlite_path) if cfg.backend == "sqlite" else cfg.url,
        "sync_db_path": str(INSTALLED_DB),
        "active_manifest_path": str(ACTIVE_MANIFEST),
        "snapshot_id": None,
        "snapshot_created_at": None,
        "expected_sha256": None,
        "analytics_sha256": None,
        "sync_install_sha256": None,
        "sha_match": False,
        "state": "NO_ACTIVE_SNAPSHOT",
    }

    manifest: dict[str, Any] | None = None
    if ACTIVE_MANIFEST.exists():
        try:
            manifest = json.loads(ACTIVE_MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out["state"] = "MANIFEST_CORRUPT"

    if manifest:
        out["snapshot_id"] = manifest.get("snapshot_id")
        out["snapshot_created_at"] = manifest.get("created_at")
        out["expected_sha256"] = manifest.get("db_sha256")
        out["counts"] = manifest.get("counts")

    if cfg.backend == "sqlite" and cfg.sqlite_path.exists():
        out["analytics_sha256"] = sha256_file(cfg.sqlite_path)
    if INSTALLED_DB.exists():
        out["sync_install_sha256"] = sha256_file(INSTALLED_DB)

    expected = out.get("expected_sha256")
    analytics = out.get("analytics_sha256")
    installed = out.get("sync_install_sha256")

    if expected and analytics and analytics == expected:
        out["sha_match"] = True
        out["state"] = "SYNCED"
    elif expected and installed and installed == expected and analytics != expected:
        out["state"] = "DRIFT"
        out["hint"] = (
            f"Point analytics at sync DB: export MARKET_EVENTS_DATABASE_PATH={INSTALLED_DB}"
        )
    elif expected and installed and installed != expected:
        out["state"] = "INSTALL_DRIFT"
    elif manifest and not expected:
        out["state"] = "MANIFEST_INCOMPLETE"
    elif not manifest:
        out["state"] = "NO_ACTIVE_SNAPSHOT"

    return out


def format_research_sync_status() -> str:
    s = research_sync_status()
    lines = [
        "RESEARCH SYNC STATUS",
        f"  state={s['state']}",
        f"  backend={s['backend']}",
        f"  snapshot_id={s.get('snapshot_id')}",
        f"  snapshot_date={s.get('snapshot_created_at')}",
        f"  expected_sha256={s.get('expected_sha256')}",
        f"  analytics_db={s.get('analytics_db_path')}",
        f"  analytics_sha256={s.get('analytics_sha256')}",
        f"  sync_install={s.get('sync_db_path')}",
        f"  sync_sha256={s.get('sync_install_sha256')}",
        f"  sha_match={s.get('sha_match')}",
    ]
    if s.get("counts"):
        lines.append(f"  counts={s['counts']}")
    if s.get("hint"):
        lines.append(f"  hint={s['hint']}")
    return "\n".join(lines)


def format_export_result(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "RESEARCH SYNC EXPORT",
            f"  snapshot_id={manifest.get('snapshot_id')}",
            f"  created_at={manifest.get('created_at')}",
            f"  db_sha256={manifest.get('db_sha256')}",
            f"  archive={manifest.get('archive_path')}",
            f"  source={manifest.get('source_path')}",
            f"  counts={manifest.get('counts')}",
        ]
    )


def format_import_result(result: dict[str, Any]) -> str:
    lines = [
        "RESEARCH SYNC IMPORT",
        f"  snapshot_id={result.get('snapshot_id')}",
        f"  snapshot_date={result.get('created_at')}",
        f"  installed_at={result.get('installed_at')}",
        f"  db_sha256={result.get('db_sha256')} (verified)",
        f"  sync_db={result.get('sync_db_path')}",
        f"  analytics_db={result.get('analytics_db_path')}",
        f"  synced={result.get('synced')}",
    ]
    if result.get("activated_path"):
        lines.append(f"  activated={result['activated_path']}")
    if result.get("hint") or not result.get("synced"):
        lines.append(
            f"  hint=export MARKET_EVENTS_DATABASE_PATH={INSTALLED_DB} "
            "or re-run import with --activate (SQLite only)",
        )
    return "\n".join(lines)
