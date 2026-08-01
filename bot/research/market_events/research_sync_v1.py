"""Research Sync V1 — portable SQLite snapshots for reproducible analytics."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR, DEFAULT_DB_PATH
from bot.research.market_events.db_config import MarketEventsDbConfig, resolve_market_events_db_config
from bot.research.market_events.event_schema import SCHEMA_VERSION

MANIFEST_VERSION = 1
RESEARCH_SYNC_DIR = BASE_DIR / "data" / "research_sync"
SNAPSHOTS_DIR = BASE_DIR / "data" / "research_snapshots"
ACTIVE_MANIFEST = RESEARCH_SYNC_DIR / "active_manifest.json"
INSTALLED_DB = RESEARCH_SYNC_DIR / "market_events.db"

RESEARCH_ANALYTICS_DB_ENV = "MARKET_EVENTS_RESEARCH_ANALYTICS_DB"


def resolve_research_analytics_sqlite_path() -> tuple[Path, str, MarketEventsDbConfig]:
    """Canonical SQLite for research export, trade-statistics, and sync doctor."""
    override = os.getenv(RESEARCH_ANALYTICS_DB_ENV, "").strip()
    cfg = resolve_market_events_db_config()
    if cfg.backend != "sqlite":
        raise ResearchSyncError(
            "Research analytics requires SQLite (or set MARKET_EVENTS_DB_BACKEND=sqlite). "
            "PostgreSQL export is not supported in Research Sync V1.",
        )
    if override:
        path = Path(override).expanduser().resolve()
        return path, f"env:{RESEARCH_ANALYTICS_DB_ENV}", cfg
    return cfg.sqlite_path.expanduser().resolve(), cfg.config_source, cfg


def research_db_candidate_paths() -> list[tuple[str, Path]]:
    """Known locations to compare in doctor (deduped)."""
    paths: list[tuple[str, Path]] = []
    try:
        analytics, src, _ = resolve_research_analytics_sqlite_path()
        paths.append((f"analytics ({src})", analytics))
    except ResearchSyncError:
        pass
    cfg = resolve_market_events_db_config()
    if cfg.backend == "sqlite":
        paths.append(("live_config", cfg.sqlite_path.expanduser().resolve()))
    paths.extend(
        [
            ("default_repo", DEFAULT_DB_PATH.resolve()),
            ("research_sync_install", INSTALLED_DB.resolve()),
        ]
    )
    data_dir = BASE_DIR / "data"
    if data_dir.is_dir():
        for p in sorted(data_dir.glob("*.db")):
            paths.append((f"data/{p.name}", p.resolve()))
    seen: set[str] = set()
    out: list[tuple[str, Path]] = []
    for label, p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append((label, p))
    return out


class ResearchSyncError(Exception):
    pass


class ResearchSyncActivateAborted(ResearchSyncError):
    """Raised when --activate fails Research Sync Safety V2 guards."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_backup_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


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
        queries = (
            ("s55_closed", "SELECT COUNT(*) AS n FROM market_events_trade_features_s55 WHERE closed_at IS NOT NULL"),
            (
                "s55_closed_pnl",
                "SELECT COUNT(*) AS n FROM market_events_trade_features_s55 "
                "WHERE closed_at IS NOT NULL AND pnl_pct IS NOT NULL",
            ),
            ("s55_all", "SELECT COUNT(*) AS n FROM market_events_trade_features_s55"),
            (
                "paper_trades_s42_closed",
                "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 WHERE status = 'CLOSED'",
            ),
            (
                "paper_trades_s42_open",
                "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42 WHERE status = 'OPEN'",
            ),
            ("paper_trades_s42_all", "SELECT COUNT(*) AS n FROM market_events_paper_trades_s42"),
        )
        for table, sql in queries:
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


def _file_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    return {
        "exists": True,
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "sha256": sha256_file(path),
        "counts": _snapshot_counts(path),
    }


def collect_activate_db_stats(path: Path) -> dict[str, Any]:
    """Fingerprint used by Research Sync Safety V2 (preflight + post-check)."""
    if not path.exists():
        return {
            "exists": False,
            "path": str(path),
            "size": 0,
            "sha": None,
            "mtime": None,
            "inode": None,
            "closed": 0,
            "open": 0,
            "trades": 0,
        }
    st = path.stat()
    counts = _snapshot_counts(path)
    return {
        "exists": True,
        "path": str(path.resolve()),
        "size": int(st.st_size),
        "sha": sha256_file(path),
        "mtime": float(st.st_mtime),
        "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "inode": int(st.st_ino),
        "closed": int(counts.get("paper_trades_s42_closed") or 0),
        "open": int(counts.get("paper_trades_s42_open") or 0),
        "trades": int(counts.get("paper_trades_s42_all") or 0),
    }


def assess_activate_safety(
    production: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[str]:
    """Return abort reasons. Empty list => activate allowed (without --force)."""
    if not production.get("exists"):
        return []
    reasons: list[str] = []
    prod_closed = int(production.get("closed") or 0)
    snap_closed = int(snapshot.get("closed") or 0)
    if prod_closed > snap_closed:
        reasons.append(
            f"production.closed ({prod_closed}) > snapshot.closed ({snap_closed})"
        )
    if snap_closed < prod_closed:
        reasons.append(
            f"snapshot has fewer CLOSED trades ({snap_closed} < {prod_closed})"
        )
    prod_mtime = production.get("mtime")
    snap_mtime = snapshot.get("mtime")
    if prod_mtime is not None and snap_mtime is not None and float(snap_mtime) < float(prod_mtime):
        reasons.append("snapshot older than production (mtime)")
    prod_size = int(production.get("size") or 0)
    snap_size = int(snapshot.get("size") or 0)
    if snap_size < prod_size:
        reasons.append(f"snapshot smaller than production ({snap_size} < {prod_size} bytes)")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def format_activate_preflight(
    production: dict[str, Any],
    snapshot: dict[str, Any],
) -> str:
    lines = [
        "RESEARCH SYNC ACTIVATE PREFLIGHT",
        f"production trades: {production.get('trades')} (CLOSED={production.get('closed')} OPEN={production.get('open')})",
        f"snapshot trades: {snapshot.get('trades')} (CLOSED={snapshot.get('closed')} OPEN={snapshot.get('open')})",
        f"production size: {production.get('size')}",
        f"snapshot size: {snapshot.get('size')}",
        f"production sha: {production.get('sha')}",
        f"snapshot sha: {snapshot.get('sha')}",
        f"production mtime: {production.get('mtime_iso') or production.get('mtime')}",
        f"snapshot mtime: {snapshot.get('mtime_iso') or snapshot.get('mtime')}",
        f"production inode: {production.get('inode')}",
        f"snapshot inode: {snapshot.get('inode')}",
    ]
    return "\n".join(lines)


def auto_backup_path_for(target: Path, *, stamp: str | None = None) -> Path:
    """``market_events.db.auto_backup.YYYYMMDD_HHMMSS`` next to the live DB."""
    ts = stamp or _utc_backup_stamp()
    return target.with_name(f"{target.name}.auto_backup.{ts}")


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
    src, src_label, cfg = resolve_research_analytics_sqlite_path()
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
    manifest["source_label"] = src_label
    manifest["export_db_absolute_path"] = str(src.resolve())
    return manifest


def import_research_snapshot(
    archive: Path,
    *,
    activate: bool = False,
    force: bool = False,
    print_fn: Any | None = print,
) -> dict[str, Any]:
    if not archive.exists():
        raise ResearchSyncError(f"snapshot not found: {archive}")
    if force and not activate:
        raise ResearchSyncError("--force requires --activate")

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
    analytics_path, _, _ = resolve_research_analytics_sqlite_path()
    result["analytics_db_path"] = str(analytics_path)
    result["sync_db_path"] = str(INSTALLED_DB)
    result["synced"] = (
        sha256_file(analytics_path) == manifest["db_sha256"] if analytics_path.exists() else False
    )
    result["force"] = bool(force)

    if activate:
        _activate_research_snapshot_into_analytics(
            result,
            analytics_path=analytics_path,
            force=bool(force),
            print_fn=print_fn if print_fn is not None else (lambda *_a, **_k: None),
        )

    return result


def _activate_research_snapshot_into_analytics(
    result: dict[str, Any],
    *,
    analytics_path: Path,
    force: bool,
    print_fn: Any,
) -> None:
    """Safety V2: preflight → backup → overwrite → verify → optional rollback."""
    cfg = resolve_market_events_db_config()
    if cfg.backend != "sqlite":
        raise ResearchSyncError("--activate requires SQLite backend")

    target = Path(analytics_path)
    snapshot_path = Path(INSTALLED_DB)
    if not snapshot_path.exists():
        raise ResearchSyncError(f"installed snapshot DB missing: {snapshot_path}")

    _checkpoint_sqlite(target)
    _checkpoint_sqlite(snapshot_path)

    production = collect_activate_db_stats(target)
    snapshot = collect_activate_db_stats(snapshot_path)
    result["production_pre"] = production
    result["snapshot_pre"] = snapshot

    preflight = format_activate_preflight(production, snapshot)
    result["activate_preflight"] = preflight
    print_fn(preflight)

    reasons = assess_activate_safety(production, snapshot)
    result["activate_abort_reasons"] = reasons
    if reasons and not force:
        msg = "ACTIVATE ABORTED (Research Sync Safety V2):\n  - " + "\n  - ".join(reasons)
        result["activated"] = False
        result["aborted"] = True
        print_fn(msg)
        raise ResearchSyncActivateAborted(msg)
    if reasons and force:
        print_fn(
            "WARNING: --force bypassing Safety V2 guards:\n  - " + "\n  - ".join(reasons)
        )
        result["force_bypass_reasons"] = reasons

    auto_bak: Path | None = None
    if target.exists() and target.resolve() != snapshot_path.resolve():
        auto_bak = auto_backup_path_for(target)
        shutil.copy2(target, auto_bak)
        result["auto_backup"] = str(auto_bak)
        # Keep legacy key for older tooling.
        result["replaced_backup"] = str(auto_bak)
        print_fn(f"auto_backup: {auto_bak}")

    before_closed = int(production.get("closed") or 0)
    shutil.copy2(snapshot_path, target)
    result["activated_path"] = str(target.resolve())
    result["activated"] = True
    result["synced"] = True
    result["aborted"] = False

    post = collect_activate_db_stats(target)
    result["production_post"] = post
    post_lines = [
        "RESEARCH SYNC ACTIVATE POST-CHECK",
        f"CLOSED: {post.get('closed')}",
        f"OPEN: {post.get('open')}",
        f"sha: {post.get('sha')}",
        f"inode: {post.get('inode')}",
        f"size: {post.get('size')}",
    ]
    result["activate_postcheck"] = "\n".join(post_lines)
    print_fn(result["activate_postcheck"])

    after_closed = int(post.get("closed") or 0)
    if after_closed < before_closed:
        if auto_bak is None or not auto_bak.exists():
            raise ResearchSyncError(
                f"CLOSED decreased ({before_closed} → {after_closed}) but auto-backup missing"
            )
        shutil.copy2(auto_bak, target)
        restored = collect_activate_db_stats(target)
        result["rolled_back"] = True
        result["rollback_from"] = str(auto_bak)
        result["production_post_rollback"] = restored
        result["synced"] = False
        print_fn(
            f"ROLLBACK: CLOSED decreased ({before_closed} → {after_closed}); "
            f"restored from {auto_bak}"
        )
        print_fn(
            "RESEARCH SYNC ACTIVATE POST-ROLLBACK\n"
            f"CLOSED: {restored.get('closed')}\n"
            f"OPEN: {restored.get('open')}\n"
            f"sha: {restored.get('sha')}\n"
            f"inode: {restored.get('inode')}"
        )
    else:
        result["rolled_back"] = False


def research_sync_status() -> dict[str, Any]:
    try:
        analytics_path, analytics_source, cfg = resolve_research_analytics_sqlite_path()
    except ResearchSyncError as exc:
        analytics_path = None
        analytics_source = str(exc)
        cfg = resolve_market_events_db_config()

    manifest_exists = ACTIVE_MANIFEST.exists()
    sync_db_exists = INSTALLED_DB.exists()

    out: dict[str, Any] = {
        "backend": cfg.backend,
        "analytics_db_path": str(analytics_path) if analytics_path else None,
        "analytics_db_source": analytics_source,
        "export_db_path": str(analytics_path) if analytics_path else None,
        "import_db_path": str(INSTALLED_DB.resolve()),
        "sync_db_path": str(INSTALLED_DB.resolve()),
        "active_manifest_path": str(ACTIVE_MANIFEST.resolve()),
        "active_manifest_exists": manifest_exists,
        "sync_db_exists": sync_db_exists,
        "snapshot_id": None,
        "snapshot_created_at": None,
        "expected_sha256": None,
        "analytics_sha256": None,
        "sync_install_sha256": None,
        "analytics_counts": None,
        "sha_match": False,
        "state": "NO_ACTIVE_SNAPSHOT",
        "state_reason": None,
    }

    manifest: dict[str, Any] | None = None
    if manifest_exists:
        try:
            manifest = json.loads(ACTIVE_MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out["state"] = "MANIFEST_CORRUPT"
            out["state_reason"] = f"Could not parse {ACTIVE_MANIFEST}"
    else:
        out["state_reason"] = (
            f"Missing {ACTIVE_MANIFEST}. Import a snapshot to activate research sync."
        )

    if manifest:
        out["snapshot_id"] = manifest.get("snapshot_id")
        out["snapshot_created_at"] = manifest.get("created_at")
        out["expected_sha256"] = manifest.get("db_sha256")
        out["counts"] = manifest.get("counts")

    if analytics_path and analytics_path.exists():
        out["analytics_sha256"] = sha256_file(analytics_path)
        out["analytics_counts"] = _snapshot_counts(analytics_path)
    if sync_db_exists:
        out["sync_install_sha256"] = sha256_file(INSTALLED_DB)

    expected = out.get("expected_sha256")
    analytics = out.get("analytics_sha256")
    installed = out.get("sync_install_sha256")

    if expected and analytics and analytics == expected:
        out["sha_match"] = True
        out["state"] = "SYNCED"
        out["state_reason"] = "Analytics DB SHA256 matches active manifest."
    elif expected and installed and installed == expected and analytics != expected:
        out["state"] = "DRIFT"
        out["state_reason"] = (
            "Installed sync DB matches manifest but analytics DB differs "
            f"({analytics_path})."
        )
        out["hint"] = (
            f"python -m bot.research.market_events research-sync-import "
            f"--file <snapshot.tar.gz> --activate"
        )
        out["hint_env"] = f"export MARKET_EVENTS_DATABASE_PATH={INSTALLED_DB}"
    elif expected and installed and installed != expected:
        out["state"] = "INSTALL_DRIFT"
        out["state_reason"] = "research_sync/market_events.db differs from manifest."
    elif manifest and not expected:
        out["state"] = "MANIFEST_INCOMPLETE"
        out["state_reason"] = "active_manifest.json missing db_sha256."
    elif not manifest:
        out["state"] = "NO_ACTIVE_SNAPSHOT"
        if not sync_db_exists:
            out["state_reason"] = (
                f"No active manifest and no {INSTALLED_DB}. "
                "Run research-sync-import to install a snapshot."
            )
        out["activate_command"] = (
            "python -m bot.research.market_events research-sync-import "
            "--file <snapshot.tar.gz> --activate"
        )

    return out


def format_research_sync_status() -> str:
    s = research_sync_status()
    lines = [
        "RESEARCH SYNC STATUS",
        f"  state={s['state']}",
        f"  state_reason={s.get('state_reason')}",
        f"  backend={s['backend']}",
        f"  active_manifest_exists={s.get('active_manifest_exists')}  path={s.get('active_manifest_path')}",
        f"  sync_db_exists={s.get('sync_db_exists')}  path={s.get('sync_db_path')}",
        f"  analytics_db_source={s.get('analytics_db_source')}",
        f"  analytics_db={s.get('analytics_db_path')}",
        f"  export_db={s.get('export_db_path')}",
        f"  import_db={s.get('import_db_path')}",
        f"  snapshot_id={s.get('snapshot_id')}",
        f"  snapshot_date={s.get('snapshot_created_at')}",
        f"  expected_sha256={s.get('expected_sha256')}",
        f"  analytics_sha256={s.get('analytics_sha256')}",
        f"  sync_sha256={s.get('sync_install_sha256')}",
        f"  sha_match={s.get('sha_match')}",
    ]
    if s.get("analytics_counts"):
        lines.append(f"  analytics_counts={s['analytics_counts']}")
    if s.get("counts"):
        lines.append(f"  manifest_counts={s['counts']}")
    if s.get("hint"):
        lines.append(f"  hint={s['hint']}")
    if s.get("hint_env"):
        lines.append(f"  hint_env={s['hint_env']}")
    if s.get("activate_command"):
        lines.append(f"  activate={s['activate_command']}")
    return "\n".join(lines)


def build_research_sync_doctor() -> dict[str, Any]:
    try:
        export_path, export_source, _ = resolve_research_analytics_sqlite_path()
    except ResearchSyncError as exc:
        export_path = None
        export_source = str(exc)
    candidates = []
    for label, path in research_db_candidate_paths():
        meta = _file_meta(path)
        meta["label"] = label
        meta["is_export_db"] = export_path is not None and path.resolve() == export_path.resolve()
        candidates.append(meta)
    return {
        "export_db_path": str(export_path.resolve()) if export_path else None,
        "export_db_source": export_source,
        "import_db_path": str(INSTALLED_DB.resolve()),
        "analytics_db_path": str(export_path.resolve()) if export_path else None,
        "active_manifest": {
            "exists": ACTIVE_MANIFEST.exists(),
            "path": str(ACTIVE_MANIFEST.resolve()),
        },
        "candidates": candidates,
        "env_hint": (
            f"Set {RESEARCH_ANALYTICS_DB_ENV}=/absolute/path/to/market_events.db "
            "if export uses the wrong file."
        ),
    }


def format_research_sync_doctor() -> str:
    doc = build_research_sync_doctor()
    lines = [
        "RESEARCH SYNC DOCTOR",
        f"  export_db_path={doc.get('export_db_path')}",
        f"  export_db_source={doc.get('export_db_source')}",
        f"  analytics_db_path={doc.get('analytics_db_path')}",
        f"  import_db_path={doc.get('import_db_path')}",
        f"  env_hint={doc.get('env_hint')}",
        "",
        "  DB candidates (compare s55_closed / paper_trades_s42_closed):",
    ]
    for c in doc.get("candidates") or []:
        flag = " [EXPORT]" if c.get("is_export_db") else ""
        if not c.get("exists"):
            lines.append(f"    - {c.get('label')}{flag}: missing {c.get('path')}")
            continue
        counts = c.get("counts") or {}
        lines.append(
            f"    - {c.get('label')}{flag}: {c.get('path')}  "
            f"size_mb={c.get('size_mb')}  sha={str(c.get('sha256', ''))[:16]}…  "
            f"s55_closed={counts.get('s55_closed')}  "
            f"s55_closed_pnl={counts.get('s55_closed_pnl')}  "
            f"s42_closed={counts.get('paper_trades_s42_closed')}"
        )
    best_n = -1
    best_label = None
    export_n = -1
    for c in doc.get("candidates") or []:
        if not c.get("exists"):
            continue
        n = int((c.get("counts") or {}).get("s55_closed") or 0)
        if c.get("is_export_db"):
            export_n = n
        if n > best_n:
            best_n = n
            best_label = c.get("label")
    if best_label and best_n > export_n:
        lines.append(
            f"  warning=Candidate '{best_label}' has more s55_closed ({best_n}) than export DB ({export_n}). "
            f"Set {RESEARCH_ANALYTICS_DB_ENV} to that path before export."
        )
    return "\n".join(lines)


def format_export_result(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "RESEARCH SYNC EXPORT",
            f"  snapshot_id={manifest.get('snapshot_id')}",
            f"  created_at={manifest.get('created_at')}",
            f"  db_sha256={manifest.get('db_sha256')}",
            f"  export_db_absolute_path={manifest.get('export_db_absolute_path') or manifest.get('source_path')}",
            f"  archive={manifest.get('archive_path')}",
            f"  source={manifest.get('source_path')}",
            f"  source_label={manifest.get('source_label')}",
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
        f"  force={result.get('force')}",
        f"  aborted={result.get('aborted')}",
        f"  rolled_back={result.get('rolled_back')}",
    ]
    if result.get("auto_backup"):
        lines.append(f"  auto_backup={result['auto_backup']}")
    if result.get("activated_path"):
        lines.append(f"  activated={result['activated_path']}")
    if result.get("activate_abort_reasons"):
        lines.append(f"  abort_reasons={result['activate_abort_reasons']}")
    if result.get("hint") or not result.get("synced"):
        lines.append(
            f"  hint=export MARKET_EVENTS_DATABASE_PATH={INSTALLED_DB} "
            "or re-run import with --activate (SQLite only; Safety V2 guards apply; "
            "--force to bypass)",
        )
    return "\n".join(lines)
