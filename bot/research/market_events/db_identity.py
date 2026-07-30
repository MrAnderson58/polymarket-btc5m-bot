"""SQLite database identity fingerprints for analytics/CLI consistency checks.

Used to prove that paper-performance (LIVE S42) and any S42 research report
open the same physical file — not a sibling research DB, ATTACH, or env shadow.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bot.research.market_events.db_config import (
    MarketEventsDbConfig,
    resolve_market_events_db_config,
)

S42_TABLE = "market_events_paper_trades_s42"
DEFAULT_SHA_BYTES = 8 * 1024 * 1024  # first 8 MiB


@dataclass(frozen=True)
class SqliteDbIdentity:
    """Physical identity of one SQLite file + optional S42 counts."""

    role: str
    absolute_path: str
    exists: bool
    inode: int | None
    size_bytes: int | None
    sha256_prefix: str | None
    sha_bytes: int
    pragma_database_list: list[tuple[Any, ...]]
    s42_total: int | None
    s42_closed: int | None
    config_source: str | None
    backend: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_prefix(path: Path, *, nbytes: int = DEFAULT_SHA_BYTES) -> str:
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


def fingerprint_sqlite_path(
    path: Path,
    *,
    role: str,
    sha_bytes: int = DEFAULT_SHA_BYTES,
    include_s42_counts: bool = True,
    config_source: str | None = None,
    backend: str | None = "sqlite",
) -> SqliteDbIdentity:
    """Build identity from an absolute path (opens DB read-only when present)."""
    resolved = path.expanduser().resolve()
    exists = resolved.exists()
    inode: int | None = None
    size_bytes: int | None = None
    sha: str | None = None
    db_list: list[tuple[Any, ...]] = []
    s42_total: int | None = None
    s42_closed: int | None = None

    if exists:
        st = resolved.stat()
        inode = int(st.st_ino)
        size_bytes = int(st.st_size)
        if size_bytes > 0 and sha_bytes > 0:
            sha = _sha256_prefix(resolved, nbytes=sha_bytes)
        try:
            conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
            try:
                db_list = [tuple(r) for r in conn.execute("PRAGMA database_list").fetchall()]
                if include_s42_counts:
                    has = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (S42_TABLE,),
                    ).fetchone()
                    if has:
                        s42_total = int(
                            conn.execute(f"SELECT COUNT(*) FROM {S42_TABLE}").fetchone()[0]
                        )
                        s42_closed = int(
                            conn.execute(
                                f"SELECT COUNT(*) FROM {S42_TABLE} WHERE status='CLOSED'",
                            ).fetchone()[0]
                        )
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    return SqliteDbIdentity(
        role=role,
        absolute_path=str(resolved),
        exists=exists,
        inode=inode,
        size_bytes=size_bytes,
        sha256_prefix=sha,
        sha_bytes=sha_bytes,
        pragma_database_list=db_list,
        s42_total=s42_total,
        s42_closed=s42_closed,
        config_source=config_source,
        backend=backend,
    )


def fingerprint_live_config(
    cfg: MarketEventsDbConfig | None = None,
    *,
    sha_bytes: int = DEFAULT_SHA_BYTES,
) -> SqliteDbIdentity:
    """Identity of the LIVE DB resolved by ``resolve_market_events_db_config``."""
    cfg = cfg or resolve_market_events_db_config()
    if cfg.backend != "sqlite":
        return SqliteDbIdentity(
            role="live_config",
            absolute_path=cfg.url,
            exists=False,
            inode=None,
            size_bytes=None,
            sha256_prefix=None,
            sha_bytes=sha_bytes,
            pragma_database_list=[],
            s42_total=None,
            s42_closed=None,
            config_source=cfg.config_source,
            backend=cfg.backend,
        )
    return fingerprint_sqlite_path(
        cfg.sqlite_path,
        role="live_config",
        sha_bytes=sha_bytes,
        config_source=cfg.config_source,
        backend=cfg.backend,
    )


def fingerprint_open_connection(
    conn: Any,
    *,
    role: str,
    sha_bytes: int = DEFAULT_SHA_BYTES,
    config_source: str | None = None,
    backend: str | None = None,
) -> SqliteDbIdentity:
    """Identity of an already-open connection via PRAGMA database_list."""
    rows = [tuple(r) for r in conn.execute("PRAGMA database_list").fetchall()]
    main = next((r for r in rows if str(r[1]) == "main"), None)
    file_path = Path(str(main[2])) if main and main[2] else None
    if file_path is None or not str(file_path):
        return SqliteDbIdentity(
            role=role,
            absolute_path=":memory: or unnamed",
            exists=False,
            inode=None,
            size_bytes=None,
            sha256_prefix=None,
            sha_bytes=sha_bytes,
            pragma_database_list=rows,
            s42_total=None,
            s42_closed=None,
            config_source=config_source,
            backend=backend or "sqlite",
        )
    ident = fingerprint_sqlite_path(
        file_path,
        role=role,
        sha_bytes=sha_bytes,
        config_source=config_source,
        backend=backend or "sqlite",
    )
    # Prefer live PRAGMA list from the open connection (detects ATTACH).
    return SqliteDbIdentity(
        role=role,
        absolute_path=ident.absolute_path,
        exists=ident.exists,
        inode=ident.inode,
        size_bytes=ident.size_bytes,
        sha256_prefix=ident.sha256_prefix,
        sha_bytes=ident.sha_bytes,
        pragma_database_list=rows,
        s42_total=ident.s42_total,
        s42_closed=ident.s42_closed,
        config_source=config_source,
        backend=backend or "sqlite",
    )


def identities_match(a: SqliteDbIdentity, b: SqliteDbIdentity) -> bool:
    """Same physical SQLite file (path + inode + size + sha prefix)."""
    if a.backend == "postgresql" or b.backend == "postgresql":
        return a.absolute_path == b.absolute_path and a.backend == b.backend
    return (
        a.exists
        and b.exists
        and a.absolute_path == b.absolute_path
        and a.inode == b.inode
        and a.size_bytes == b.size_bytes
        and a.sha256_prefix == b.sha256_prefix
    )


def format_identity(ident: SqliteDbIdentity) -> str:
    lines = [
        f"role: {ident.role}",
        f"backend: {ident.backend}",
        f"config_source: {ident.config_source}",
        f"absolute_path: {ident.absolute_path}",
        f"exists: {ident.exists}",
        f"inode: {ident.inode}",
        f"size_bytes: {ident.size_bytes}",
        f"sha256_first_{ident.sha_bytes}_bytes: {ident.sha256_prefix}",
        f"PRAGMA database_list: {ident.pragma_database_list}",
        f"S42 COUNT(*): {ident.s42_total}",
        f"S42 CLOSED: {ident.s42_closed}",
    ]
    return "\n".join(lines)


def collect_s42_analytics_identities(
    *,
    sha_bytes: int = DEFAULT_SHA_BYTES,
) -> dict[str, SqliteDbIdentity]:
    """Fingerprints for every LIVE path that must agree on S42 analytics.

    Research sibling DB is included under ``research_sibling`` for contrast —
    it is allowed to differ (S60 separation). S42 paper CLIs must match ``live``.
    """
    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.signal_intelligence.research_repository_s60 import (
        resolve_research_db_config,
    )

    live_cfg = resolve_market_events_db_config()
    out: dict[str, SqliteDbIdentity] = {
        "live_config": fingerprint_live_config(live_cfg, sha_bytes=sha_bytes),
    }

    if live_cfg.backend == "sqlite":
        with market_events_connection() as conn:
            out["market_events_connection"] = fingerprint_open_connection(
                conn,
                role="market_events_connection",
                sha_bytes=sha_bytes,
                config_source=live_cfg.config_source,
                backend=live_cfg.backend,
            )
            # Same connection path paper-performance / learning-health use.
            out["paper_performance_cli"] = fingerprint_open_connection(
                conn,
                role="paper_performance_cli",
                sha_bytes=sha_bytes,
                config_source=live_cfg.config_source,
                backend=live_cfg.backend,
            )
            out["s42_research_report"] = fingerprint_open_connection(
                conn,
                role="s42_research_report",
                sha_bytes=sha_bytes,
                config_source=live_cfg.config_source,
                backend=live_cfg.backend,
            )

    research = resolve_research_db_config()
    if research.backend == "sqlite" and research.sqlite_path is not None:
        out["research_sibling"] = fingerprint_sqlite_path(
            research.sqlite_path,
            role="research_sibling",
            sha_bytes=sha_bytes,
            config_source=research.config_source,
            backend=research.backend,
        )
    return out


def assert_s42_cli_and_report_same_db(
    identities: dict[str, SqliteDbIdentity] | None = None,
) -> dict[str, SqliteDbIdentity]:
    """Raise AssertionError if S42 CLI and S42 research report resolve different DBs."""
    ids = identities or collect_s42_analytics_identities()
    required = ("live_config", "market_events_connection", "paper_performance_cli", "s42_research_report")
    missing = [k for k in required if k not in ids]
    if missing:
        raise AssertionError(f"missing identities: {missing}")

    base = ids["live_config"]
    for key in required[1:]:
        other = ids[key]
        if not identities_match(base, other):
            raise AssertionError(
                "S42 analytics DB mismatch:\n"
                f"--- {base.role} ---\n{format_identity(base)}\n"
                f"--- {other.role} ---\n{format_identity(other)}\n"
            )
        if base.s42_total != other.s42_total or base.s42_closed != other.s42_closed:
            raise AssertionError(
                f"S42 COUNT mismatch between {base.role} and {other.role}: "
                f"total {base.s42_total}/{other.s42_total} "
                f"closed {base.s42_closed}/{other.s42_closed}"
            )

    # ATTACH would show extra rows in database_list beyond main.
    for key in required:
        db_list = ids[key].pragma_database_list
        names = {str(r[1]) for r in db_list}
        if names and names != {"main"}:
            raise AssertionError(
                f"{key} has unexpected ATTACH databases: {db_list}"
            )

    return ids


__all__ = [
    "DEFAULT_SHA_BYTES",
    "S42_TABLE",
    "SqliteDbIdentity",
    "assert_s42_cli_and_report_same_db",
    "collect_s42_analytics_identities",
    "fingerprint_live_config",
    "fingerprint_open_connection",
    "fingerprint_sqlite_path",
    "format_identity",
    "identities_match",
]
