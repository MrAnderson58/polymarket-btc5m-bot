"""Nightly AI-server backup — SQLite + JSON + Reports + git diff; 30-day retention."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.ops.server_infra_v1.config import BACKUP_RETENTION_DAYS, BACKUP_ROOT, REPO
from bot.research.market_events.config import MARKET_EVENTS_DATABASE_PATH


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _copy_sqlite(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        dest.write_text(f"missing:{src}\n", encoding="utf-8")
        return
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _git_diff_bundle(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    parts = []
    for args in (
        ["git", "status", "--short"],
        ["git", "rev-parse", "HEAD"],
        ["git", "branch", "--show-current"],
        ["git", "diff", "--stat"],
        ["git", "diff"],
    ):
        try:
            out = subprocess.check_output(args, cwd=str(REPO), text=True, stderr=subprocess.STDOUT)
        except Exception as exc:
            out = f"ERROR {args}: {exc}\n"
        parts.append(f"$ {' '.join(args)}\n{out}\n")
    dest.write_text("\n".join(parts), encoding="utf-8")


def _prune(root: Path, *, days: int) -> int:
    if not root.exists():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for p in root.glob("*.tar.gz"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def run_backup() -> dict[str, Any]:
    t0 = time.time()
    stamp = _stamp()
    work = BACKUP_ROOT / f"stage_{stamp}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    # SQLite
    sqlite_dir = work / "sqlite"
    me_db = Path(MARKET_EVENTS_DATABASE_PATH)
    _copy_sqlite(me_db, sqlite_dir / "market_events.db")
    trades = REPO / "data" / "trades.db"
    if trades.exists():
        _copy_sqlite(trades, sqlite_dir / "trades.db")

    # JSON research artifacts (small)
    json_dir = work / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    for name in ("RESEARCH_PACKAGE.json",):
        src = REPO / name
        if src.exists() and src.stat().st_size < 5_000_000:
            shutil.copy2(src, json_dir / name)

    # Reports (markdown heads / selected)
    reports_dir = work / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "RESEARCH_CONCLUSION.md",
        "NEXT_RESEARCH.md",
        "DAILY_SCORECARD.md",
        "INTEGRITY_REPORT.md",
        "REALITY_REPORT.md",
    ):
        src = REPO / name
        if src.exists() and src.stat().st_size < 5_000_000:
            shutil.copy2(src, reports_dir / name)

    # Git diff
    _git_diff_bundle(work / "git" / "diff_bundle.txt")

    archive = BACKUP_ROOT / f"ai_server_{stamp}.tar.gz"
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(work, arcname=f"ai_server_{stamp}")
    shutil.rmtree(work, ignore_errors=True)
    removed = _prune(BACKUP_ROOT, days=BACKUP_RETENTION_DAYS)
    elapsed = round(time.time() - t0, 3)
    terminal = "\n".join([
        "AI SERVER BACKUP V1",
        "",
        f"archive={archive}",
        f"bytes={archive.stat().st_size}",
        f"retention_days={BACKUP_RETENTION_DAYS} pruned={removed}",
        f"elapsed={elapsed}s",
        "",
    ])
    return {
        "ok": True,
        "archive": str(archive),
        "bytes": archive.stat().st_size,
        "pruned": removed,
        "elapsed_sec": elapsed,
        "terminal": terminal,
    }
