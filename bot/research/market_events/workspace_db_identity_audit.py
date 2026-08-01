"""Workspace DB Identity Audit — prove Cursor vs Terminal SQLite identity.

Uses the same resolver as ``market-math-research``:
``resolve_research_analytics_sqlite_path``.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bot.research.market_events.research_sync_v1 import (
    resolve_research_analytics_sqlite_path,
)

S42 = "market_events_paper_trades_s42"
SHA_BYTES = 16 * 1024 * 1024  # first 16 MiB


@dataclass(frozen=True)
class FoundDb:
    absolute_path: str
    size: int
    inode: int
    closed_trades: int | None
    error: str | None = None


def _sha256_first_n(path: Path, *, nbytes: int = SHA_BYTES) -> str:
    h = hashlib.sha256()
    remaining = nbytes
    with path.open("rb") as fh:
        while remaining > 0:
            chunk = fh.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def _closed_count(path: Path) -> tuple[int | None, str | None]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (S42,),
            ).fetchone()
            if not has:
                return None, f"no table {S42}"
            n = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {S42} WHERE status='CLOSED'",
                ).fetchone()[0]
            )
            return n, None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — audit must continue
        return None, str(exc)


def resolve_market_math_db_path() -> tuple[Path, str]:
    """Same code path as market-math-research."""
    path, source, _cfg = resolve_research_analytics_sqlite_path()
    return Path(path).expanduser().resolve(), source


def terminal_default_db_path(*, cwd: str | None = None) -> Path:
    """What a Terminal session in ``cwd`` would open as data/market_events.db."""
    base = Path(cwd or os.getcwd()).resolve()
    return (base / "data" / "market_events.db").resolve()


def find_market_events_dbs(
    *,
    roots: Iterable[str | Path] | None = None,
    timeout_sec: float = 120.0,
) -> list[FoundDb]:
    """Locate ``market_events.db`` files under search roots (disk audit)."""
    found: dict[str, FoundDb] = {}
    t0 = time.time()

    # Fast whole-disk index (macOS Spotlight), then walk known roots as fallback.
    try:
        proc = subprocess.run(
            ["mdfind", "kMDItemFSName == 'market_events.db'"],
            capture_output=True,
            text=True,
            timeout=min(90.0, timeout_sec),
            check=False,
        )
        for line in (proc.stdout or "").splitlines():
            p = Path(line.strip())
            if p.name == "market_events.db" and p.is_file():
                _register_found(found, p)
    except (subprocess.SubprocessError, OSError):
        pass

    if roots is None:
        roots = [
            Path(os.getcwd()).resolve() / "data",
            Path.home() / "polymarket-bot",
            Path("/Users") / Path.home().name / "polymarket-bot",
            Path("/tmp"),
        ]

    # Always include cwd data dir even if Spotlight is stale.
    skip_dirs = {
        ".git",
        "node_modules",
        "Library",
        "System",
        "private",
        "proc",
        "dev",
        ".Trash",
        "Caches",
        "DerivedData",
        "__pycache__",
    }

    # If Spotlight already found files, only do a shallow walk of explicit roots.
    shallow_only = bool(found)

    for root in roots:
        if time.time() - t0 > timeout_sec:
            break
        root_p = Path(root)
        if not root_p.exists():
            continue
        try:
            if shallow_only:
                for p in root_p.rglob("market_events.db"):
                    if time.time() - t0 > timeout_sec:
                        break
                    if p.is_file():
                        _register_found(found, p)
                continue
            for dirpath, dirnames, filenames in os.walk(root_p, followlinks=False):
                if time.time() - t0 > timeout_sec:
                    break
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in skip_dirs and not d.startswith(".")
                ]
                if "market_events.db" in filenames:
                    _register_found(found, Path(dirpath) / "market_events.db")
        except OSError:
            continue

    return sorted(found.values(), key=lambda x: x.absolute_path)


def _register_found(found: dict[str, FoundDb], path: Path) -> None:
    try:
        resolved = path.resolve()
        key = str(resolved)
        if key in found:
            return
        st = resolved.stat()
        closed, err = _closed_count(resolved)
        found[key] = FoundDb(
            absolute_path=key,
            size=int(st.st_size),
            inode=int(st.st_ino),
            closed_trades=closed,
            error=err,
        )
    except OSError as exc:
        found[str(path)] = FoundDb(
            absolute_path=str(path),
            size=0,
            inode=0,
            closed_trades=None,
            error=str(exc),
        )


def run_workspace_db_identity_audit(
    *,
    search_disk: bool = True,
    cwd: str | None = None,
) -> str:
    """Full audit text required by Workspace DB Identity Audit task."""
    lines: list[str] = []
    cwd_s = cwd or os.getcwd()
    try:
        git_top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd_s,
            text=True,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        git_top = "(git rev-parse failed)"

    try:
        pwd_s = subprocess.check_output(["pwd"], cwd=cwd_s, text=True).strip()
    except (subprocess.SubprocessError, OSError):
        pwd_s = cwd_s

    lines.append("=== Workspace identity ===")
    lines.append(f"os.getcwd(): {os.getcwd()}")
    lines.append(f"git rev-parse --show-toplevel: {git_top}")
    lines.append(f"pwd: {pwd_s}")
    lines.append("")

    resolved, source = resolve_market_math_db_path()
    terminal_path = terminal_default_db_path(cwd=cwd_s)

    lines.append("=== market-math-research DB (resolve_research_analytics_sqlite_path) ===")
    lines.append(f"resolved_db_path: {resolved}")
    lines.append(f"config_source: {source}")
    lines.append("")

    if not resolved.is_file():
        lines.append(f"ERROR: resolved DB does not exist: {resolved}")
    else:
        st = resolved.stat()
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        sha = _sha256_first_n(resolved, nbytes=SHA_BYTES)
        lines.append(f"os.stat(path).st_ino: {st.st_ino}")
        lines.append(f"size: {st.st_size}")
        lines.append(f"mtime: {mtime}")
        lines.append(f"sha256(first 16MB): {sha}")
        lines.append("")

        conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        try:
            closed_n = conn.execute(
                f"SELECT COUNT(*) FROM {S42} WHERE status='CLOSED'",
            ).fetchone()[0]
            lines.append("SQL: SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE status='CLOSED';")
            lines.append(f"result: {closed_n}")
            lines.append("")

            agg = conn.execute(
                f"""
                SELECT SUM(pnl_usd), AVG(pnl_pct), COUNT(*)
                FROM {S42}
                WHERE status='CLOSED'
                """,
            ).fetchone()
            lines.append(
                "SQL: SELECT SUM(pnl_usd), AVG(pnl_pct), COUNT(*) "
                "FROM market_events_paper_trades_s42 WHERE status='CLOSED';"
            )
            lines.append(f"SUM(pnl_usd): {agg[0]}")
            lines.append(f"AVG(pnl_pct): {agg[1]}")
            lines.append(f"COUNT(*): {agg[2]}")
            lines.append("")

            db_list = conn.execute("PRAGMA database_list").fetchall()
            lines.append("PRAGMA database_list;")
            for row in db_list:
                lines.append(f"  {tuple(row)}")
            lines.append("")

            seq = conn.execute(
                "SELECT name, seq FROM sqlite_sequence WHERE name=?",
                (S42,),
            ).fetchall()
            lines.append("sqlite_sequence for market_events_paper_trades_s42:")
            if seq:
                for row in seq:
                    lines.append(f"  name={row[0]} seq={row[1]}")
            else:
                lines.append("  (no sqlite_sequence row)")
        finally:
            conn.close()

    lines.append("")
    lines.append("=== disk search: market_events.db ===")
    if search_disk:
        found = find_market_events_dbs()
        if not found:
            lines.append("(none found)")
        for item in found:
            lines.append(f"absolute path: {item.absolute_path}")
            lines.append(f"size: {item.size}")
            lines.append(f"inode: {item.inode}")
            lines.append(f"closed trades: {item.closed_trades}")
            if item.error:
                lines.append(f"error: {item.error}")
            lines.append("")
    else:
        lines.append("(skipped)")

    same = (
        resolved.exists()
        and terminal_path.exists()
        and resolved.samefile(terminal_path)
    ) if resolved.exists() and terminal_path.exists() else (
        str(resolved) == str(terminal_path)
    )

    lines.append("=== final ===")
    lines.append("Cursor analyzed:")
    lines.append(str(resolved))
    lines.append("")
    lines.append("Terminal analyzed:")
    lines.append(str(terminal_path))
    lines.append("")
    lines.append("Same DB:")
    lines.append("YES" if same else "NO")
    return "\n".join(lines)


__all__ = [
    "SHA_BYTES",
    "find_market_events_dbs",
    "resolve_market_math_db_path",
    "run_workspace_db_identity_audit",
    "terminal_default_db_path",
]
