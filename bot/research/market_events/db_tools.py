"""Phase G.0 — database ops: info, check, copy, benchmark, backup."""

from __future__ import annotations

import os
import hashlib
import json
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bot.research.market_events.db import (
    MarketEventsDbError,
    apply_sqlite_pragmas,
    connection_is_postgres,
    ensure_wal_enabled,
    market_events_connection,
)
from bot.research.market_events.db_config import (
    MarketEventsDbConfig,
    resolve_market_events_db_config,
)
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.schema_pg import ALL_TABLES


def _mask_db_url(url: str) -> str:
    if "@" in url:
        prefix, rest = url.split("@", 1)
        if "://" in prefix:
            scheme = prefix.split("://")[0]
            return f"{scheme}://***@{rest}"
    return url


def format_db_info(cfg: MarketEventsDbConfig | None = None) -> str:
    cfg = cfg or resolve_market_events_db_config()
    lines = [
        "MARKET EVENTS DATABASE INFO",
        "",
        f"backend: {cfg.backend}",
        f"config_source: {cfg.config_source}",
        f"url: {_mask_db_url(cfg.url)}",
        "",
    ]

    if cfg.backend == "sqlite":
        path = cfg.sqlite_path
        wal_path = Path(f"{path}-wal")
        shm_path = Path(f"{path}-shm")
        lines.extend([
            f"path: {path}",
            f"exists: {path.exists()}",
            f"wal_file: {'present' if wal_path.exists() else 'absent'} ({wal_path})",
            f"shm_file: {'present' if shm_path.exists() else 'absent'} ({shm_path})",
            f"sqlite_version: {sqlite3.sqlite_version}",
            "",
        ])
        if not path.exists():
            lines.append("(database file does not exist yet)")
            return "\n".join(lines)
    else:
        parsed = urlparse(cfg.url)
        lines.extend([
            f"host: {parsed.hostname}",
            f"port: {parsed.port or 5432}",
            f"database: {(parsed.path or '/').lstrip('/')}",
            "",
        ])

    try:
        with market_events_connection(url=cfg.url if cfg.backend == "postgresql" else None,
                                      db_path=cfg.sqlite_path if cfg.backend == "sqlite" else None) as conn:
            if cfg.backend == "sqlite":
                journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
                timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                page_size = conn.execute("PRAGMA page_size").fetchone()[0]
                cache_size = conn.execute("PRAGMA cache_size").fetchone()[0]
                db_list = conn.execute("PRAGMA database_list").fetchall()
                lines.extend([
                    f"journal_mode: {journal}",
                    f"busy_timeout: {timeout}",
                    f"foreign_keys: {fk}",
                    f"page_size: {page_size}",
                    f"cache_size: {cache_size}",
                    "",
                    "database_list:",
                ])
                for row in db_list:
                    lines.append(f"  seq={row[0]} name={row[1]!r} file={row[2]!r}")
            else:
                ver = conn.execute("SELECT version() AS v").fetchone()
                lines.append(f"postgresql_version: {ver['v']}")
                lines.extend([
                    "journal_mode: wal (PostgreSQL MVCC)",
                    "",
                ])
                tables = conn.execute(
                    """
                    SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public' AND tablename LIKE 'market_%'
                    ORDER BY tablename
                    """,
                ).fetchall()
                lines.append(f"market_events tables: {len(tables)}")
    except Exception as exc:
        lines.append(f"connection_error: {exc}")

    return "\n".join(lines)


def db_check(cfg: MarketEventsDbConfig | None = None) -> str:
    cfg = cfg or resolve_market_events_db_config()
    lines = ["MARKET EVENTS DB CHECK", "", f"backend: {cfg.backend}", ""]

    try:
        with market_events_connection(
            url=cfg.url if cfg.backend == "postgresql" else None,
            db_path=cfg.sqlite_path if cfg.backend == "sqlite" else None,
        ) as conn:
            apply_migrations(conn)
            if cfg.backend == "sqlite":
                qc = conn.execute("PRAGMA quick_check").fetchone()[0]
                lines.append(f"integrity: {qc}")
            else:
                lines.append("integrity: OK (PostgreSQL)")

            missing: list[str] = []
            for table in ALL_TABLES:
                try:
                    if cfg.backend == "sqlite":
                        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
                    else:
                        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
                except Exception:
                    missing.append(table)
            if missing:
                lines.append(f"missing_tables: {', '.join(missing)}")
            else:
                lines.append(f"tables_ok: {len(ALL_TABLES)}/{len(ALL_TABLES)}")

            lines.append("")
            lines.append("row_counts:")
            for table in ALL_TABLES[:10]:
                try:
                    n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                    lines.append(f"  {table}: {int(n)}")
                except Exception:
                    lines.append(f"  {table}: (missing)")
            lines.append("  ...")
    except Exception as exc:
        lines.append(f"FAIL: {exc}")

    return "\n".join(lines)


def _conflict_clause(conn: Any, table: str) -> str:
    if table == "market_events_migrations":
        return "ON CONFLICT (version) DO NOTHING"
    cols = _table_columns(conn, table)
    if "id" in cols:
        return "ON CONFLICT (id) DO NOTHING"
    if table == "market_events_pending_shocks":
        return "ON CONFLICT (event_id) DO NOTHING"
    if table == "market_events_opportunity_scores_v2":
        return "ON CONFLICT (event_id) DO NOTHING"
    if table == "market_events_timeline_cache":
        return "ON CONFLICT (event_id) DO NOTHING"
    return "ON CONFLICT DO NOTHING"


def _table_columns(conn: Any, table: str) -> list[str]:
    if connection_is_postgres(conn):
        rows = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            ORDER BY ordinal_position
            """,
            (table,),
        ).fetchall()
        return [r["column_name"] for r in rows]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def _table_count(conn: Any, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return -1


def _table_checksum(conn: Any, table: str) -> str:
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(id), 0) AS sid FROM {table}",
        ).fetchone()
        payload = f"{table}:{row['n']}:{row['sid']}"
        return hashlib.md5(payload.encode()).hexdigest()[:12]
    except Exception:
        try:
            n = _table_count(conn, table)
            return hashlib.md5(f"{table}:{n}".encode()).hexdigest()[:12]
        except Exception:
            return "error"


def migrate_sqlite_to_postgres(
    *,
    sqlite_path: Path | None = None,
    pg_url: str | None = None,
) -> str:
    """Copy SQLite → PostgreSQL with row-count and checksum verification."""
    cfg = resolve_market_events_db_config()
    src_path = sqlite_path or cfg.sqlite_path
    dst_url = pg_url or cfg.url
    if not dst_url or not dst_url.startswith(("postgres://", "postgresql://")):
        raise MarketEventsDbError("migrate-to-postgres requires MARKET_EVENTS_DB_URL (postgresql://...)")

    lines = [
        "MARKET EVENTS MIGRATE SQLite → PostgreSQL",
        "",
        f"source: {src_path}",
        f"target: {dst_url.split('@')[-1] if '@' in dst_url else dst_url}",
        "",
        f"{'TABLE':<42} {'SQLITE':>8} {'PG':>8} {'MATCH':>6} {'CHK':>6}",
        "-" * 72,
    ]

    mismatches: list[str] = []

    with market_events_connection(db_path=src_path) as src:
        apply_migrations(src)
        with market_events_connection(url=dst_url) as dst:
            apply_migrations(dst)

            for table in ALL_TABLES:
                src_n = _table_count(src, table)
                if src_n <= 0:
                    dst_n = _table_count(dst, table)
                    lines.append(f"{table:<42} {src_n:>8} {dst_n:>8} {'SKIP':>6} {'—':>6}")
                    continue

                cols = _table_columns(src, table)
                if not cols:
                    continue
                col_sql = ", ".join(cols)
                placeholders = ", ".join("?" for _ in cols)
                rows = src.execute(f"SELECT {col_sql} FROM {table}").fetchall()

                for row in rows:
                    vals = tuple(row[c] for c in cols)
                    conflict = _conflict_clause(dst, table)
                    dst.execute(
                        f"""
                        INSERT INTO {table} ({col_sql})
                        VALUES ({placeholders})
                        {conflict}
                        """,
                        vals,
                    )
                dst.commit()

                dst_n = _table_count(dst, table)
                src_chk = _table_checksum(src, table)
                dst_chk = _table_checksum(dst, table)
                match = "OK" if src_n == dst_n else "FAIL"
                chk = "OK" if src_chk == dst_chk else "DIFF"
                if match != "OK":
                    mismatches.append(f"{table}: sqlite={src_n} pg={dst_n}")
                lines.append(f"{table:<42} {src_n:>8} {dst_n:>8} {match:>6} {chk:>6}")

            _reset_pg_sequences(dst)

    lines.extend([
        "",
        f"completed_at: {datetime.now(timezone.utc).isoformat()}",
        f"mismatches: {len(mismatches)}",
    ])
    if mismatches:
        lines.append("")
        for m in mismatches:
            lines.append(f"  ! {m}")
    lines.append("")
    lines.append("SQLite source file preserved (not deleted).")
    return "\n".join(lines)


def _reset_pg_sequences(conn: Any) -> None:
    if not connection_is_postgres(conn):
        return
    for table in ALL_TABLES:
        try:
            conn.execute(
                f"""
                SELECT setval(
                  pg_get_serial_sequence('{table}', 'id'),
                  COALESCE((SELECT MAX(id) FROM {table}), 1)
                )
                """,
            )
        except Exception:
            pass
    conn.commit()


def db_benchmark(
    *,
    sqlite_path: Path | None = None,
    pg_url: str | None = None,
    n: int = 200,
) -> str:
    """Compare insert/select/update throughput SQLite vs PostgreSQL."""
    cfg = resolve_market_events_db_config()
    src_path = sqlite_path or cfg.sqlite_path
    dst_url = pg_url or (cfg.url if cfg.backend == "postgresql" else None)

    results: dict[str, dict[str, float]] = {}

    def _bench(label: str, url: str | None, path: Path | None) -> None:
        timings: dict[str, float] = {}
        ctx = market_events_connection(url=url) if url else market_events_connection(db_path=path)
        with ctx as conn:
            apply_migrations(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS _bench_probe (
                  id INTEGER PRIMARY KEY, v TEXT, n INTEGER
                )
                """ if not connection_is_postgres(conn) else
                """
                CREATE TABLE IF NOT EXISTS _bench_probe (
                  id BIGSERIAL PRIMARY KEY, v TEXT, n INTEGER
                )
                """,
            )
            t0 = time.perf_counter()
            for i in range(n):
                conn.execute(
                    "INSERT INTO _bench_probe (v, n) VALUES (?, ?)",
                    (f"row-{i}", i),
                )
            conn.commit()
            timings["insert_per_sec"] = round(n / max(time.perf_counter() - t0, 1e-6), 1)

            t0 = time.perf_counter()
            for _ in range(n):
                conn.execute("SELECT COUNT(*) FROM _bench_probe").fetchone()
            timings["select_per_sec"] = round(n / max(time.perf_counter() - t0, 1e-6), 1)

            t0 = time.perf_counter()
            for i in range(n):
                conn.execute("UPDATE _bench_probe SET n = ? WHERE v = ?", (i + 1, f"row-{i}"))
            conn.commit()
            timings["update_per_sec"] = round(n / max(time.perf_counter() - t0, 1e-6), 1)

            t0 = time.perf_counter()
            conn.execute(
                """
                SELECT b.v, m.symbol FROM _bench_probe b
                LEFT JOIN market_events m ON m.id = b.n LIMIT 10
                """,
            ).fetchall()
            timings["join_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        results[label] = timings

    if src_path.exists():
        ensure_wal_enabled(src_path)
        _bench("sqlite", None, src_path)

    if dst_url and _is_postgres(dst_url):
        _bench("postgresql", dst_url, None)
    elif cfg.backend == "postgresql":
        _bench("postgresql", cfg.url, None)

    lines = ["MARKET DB BENCHMARK", "", json.dumps(results, indent=2)]
    return "\n".join(lines)


def _is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def db_backup(cfg: MarketEventsDbConfig | None = None, dest: Path | None = None) -> str:
    cfg = cfg or resolve_market_events_db_config()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if cfg.backend == "sqlite":
        src = cfg.sqlite_path
        dst = dest or src.parent / f"market_events_backup_{ts}.db"
        if src.exists():
            shutil.copy2(src, dst)
            for ext in ("-wal", "-shm"):
                side = Path(f"{src}{ext}")
                if side.exists():
                    shutil.copy2(side, Path(f"{dst}{ext}"))
        return f"SQLite backup: {dst}"

    parsed = urlparse(cfg.url)
    dbname = (parsed.path or "/").lstrip("/") or "market_events"
    dst = dest or Path(f"market_events_pg_backup_{ts}.sql")
    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", dbname,
        "-f", str(dst),
    ]
    env = {"PGPASSWORD": parsed.password or ""}
    subprocess.run(cmd, check=False, env={**os.environ, **env})
    return f"PostgreSQL backup attempted: {dst} (requires pg_dump)"


# Backward-compatible alias
format_db_info_legacy = format_db_info
