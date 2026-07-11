"""Phase G.0/G.0a — database ops: info, check, copy, benchmark, backup, restore."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import (
    MarketEventsDbError,
    connection_is_postgres,
    ensure_wal_enabled,
    market_events_connection,
)
from bot.research.market_events.db_config import (
    MarketEventsDbConfig,
    resolve_market_events_db_config,
)
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.schema_pg import ALL_TABLES

BACKUPS_DIR = BASE_DIR / "backups"


def _mask_db_url(url: str) -> str:
    if "@" in url:
        prefix, rest = url.split("@", 1)
        if "://" in prefix:
            scheme = prefix.split("://")[0]
            return f"{scheme}://***@{rest}"
    return url


def _conn_kwargs(cfg: MarketEventsDbConfig) -> dict[str, Any]:
    if cfg.backend == "postgresql":
        return {"url": cfg.url}
    return {"db_path": cfg.sqlite_path}


def _schema_version(conn: Any) -> int:
    try:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM market_events_migrations",
        ).fetchone()
        return int(row["v"] or 0)
    except Exception:
        return 0


def _list_migrations(conn: Any) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT version, applied_at, description FROM market_events_migrations ORDER BY version",
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _index_count(conn: Any, cfg: MarketEventsDbConfig) -> int:
    if cfg.backend == "sqlite":
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM sqlite_master
            WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
              AND (tbl_name LIKE 'market_%' OR tbl_name LIKE 'paper_%')
            """,
        ).fetchone()
        return int(row["n"] if row else 0)
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM pg_indexes
        WHERE schemaname = 'public'
          AND (tablename LIKE 'market_%' OR tablename LIKE 'paper_%')
        """,
    ).fetchone()
    return int(row["n"] if row else 0)


def _total_rows(conn: Any) -> int:
    total = 0
    for table in ALL_TABLES:
        n = _table_count(conn, table)
        if n > 0:
            total += n
    return total


def format_db_info(cfg: MarketEventsDbConfig | None = None) -> str:
    cfg = cfg or resolve_market_events_db_config()
    lines = [
        "MARKET EVENTS DATABASE INFO",
        "",
        f"backend: {cfg.backend}",
        f"database: {cfg.database_name}",
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
            f"wal_file: {'present' if wal_path.exists() else 'absent'}",
            f"shm_file: {'present' if shm_path.exists() else 'absent'}",
            f"sqlite_version: {sqlite3.sqlite_version}",
            "",
        ])
        if not path.exists():
            lines.append("(database file does not exist yet)")
            return "\n".join(lines)

    try:
        with market_events_connection(**_conn_kwargs(cfg)) as conn:
            ver = _schema_version(conn)
            lines.append(f"schema_version: {ver} (target {SCHEMA_VERSION})")
            lines.append(f"market_events_tables: {len(ALL_TABLES)}")
            lines.append(f"total_rows: {_total_rows(conn)}")

            if cfg.backend == "sqlite":
                journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
                size = path.stat().st_size if path.exists() else 0
                lines.extend([
                    f"database_size: {size / 1024 / 1024:.2f} MB",
                    f"journal_mode: {journal}",
                    f"busy_timeout: {conn.execute('PRAGMA busy_timeout').fetchone()[0]}",
                    f"foreign_keys: {conn.execute('PRAGMA foreign_keys').fetchone()[0]}",
                ])
            else:
                pg_ver = conn.execute("SELECT version() AS v").fetchone()
                size = conn.execute(
                    "SELECT pg_size_pretty(pg_database_size(current_database())) AS s",
                ).fetchone()
                active = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM pg_stat_activity
                    WHERE datname = current_database()
                    """,
                ).fetchone()
                me_tables = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM pg_tables
                    WHERE schemaname = 'public'
                      AND (tablename LIKE 'market_%' OR tablename LIKE 'paper_%')
                    """,
                ).fetchone()
                lines.extend([
                    f"postgresql_version: {pg_ver['v'].split(',')[0]}",
                    f"database_size: {size['s']}",
                    f"market_events_table_count: {int(me_tables['n'] if me_tables else 0)}",
                    f"active_connections: {int(active['n'] if active else 0)}",
                ])
    except Exception as exc:
        lines.append(f"connection_error: {exc}")

    return "\n".join(lines)


def db_check(cfg: MarketEventsDbConfig | None = None) -> str:
    cfg = cfg or resolve_market_events_db_config()
    lines = [
        "MARKET EVENTS DB CHECK",
        "",
        f"backend: {cfg.backend}",
        f"database: {cfg.database_name}",
        "",
    ]
    ok = True

    try:
        with market_events_connection(**_conn_kwargs(cfg)) as conn:
            applied = apply_migrations(conn)
            if applied:
                lines.append(f"migrations_applied: {', '.join(applied)}")
            else:
                lines.append("migrations_applied: (already up to date)")

            ver = _schema_version(conn)
            lines.append(f"schema_version: {ver} / {SCHEMA_VERSION}")
            if ver < SCHEMA_VERSION:
                lines.append("schema_status: BEHIND")
                ok = False
            else:
                lines.append("schema_status: OK")

            migrations = _list_migrations(conn)
            lines.append(f"migration_rows: {len(migrations)}")
            if migrations:
                last = migrations[-1]
                lines.append(f"latest_migration: v{last.get('version')} — {last.get('description', '')[:60]}")

            if cfg.backend == "sqlite":
                qc = conn.execute("PRAGMA quick_check").fetchone()[0]
                lines.append(f"integrity: {qc}")
                if qc != "ok":
                    ok = False
            else:
                lines.append("integrity: OK (PostgreSQL MVCC)")

            missing: list[str] = []
            for table in ALL_TABLES:
                try:
                    conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
                except Exception:
                    missing.append(table)
            if missing:
                lines.append(f"missing_tables: {len(missing)}")
                for t in missing[:5]:
                    lines.append(f"  - {t}")
                ok = False
            else:
                lines.append(f"tables_ok: {len(ALL_TABLES)}/{len(ALL_TABLES)}")

            idx_n = _index_count(conn, cfg)
            lines.append(f"indexes: {idx_n}")

            lines.extend(["", "row_counts:"])
            for table in ALL_TABLES:
                n = _table_count(conn, table)
                lines.append(f"  {table}: {max(n, 0)}")

            lines.extend(["", f"total_rows: {_total_rows(conn)}"])
            lines.append(f"overall: {'PASS' if ok else 'FAIL'}")
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
    if table == "market_event_exchange_symbols":
        return "ON CONFLICT (canonical_symbol) DO NOTHING"
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
        n = _table_count(conn, table)
        return hashlib.md5(f"{table}:{n}".encode()).hexdigest()[:12]


def migrate_sqlite_to_postgres(
    *,
    sqlite_path: Path | None = None,
    pg_url: str | None = None,
) -> str:
    """Copy SQLite → PostgreSQL (trading_ai) with row-count and checksum verification."""
    cfg = resolve_market_events_db_config()
    src_path = sqlite_path or cfg.sqlite_path
    dst_url = pg_url or cfg.url
    if not dst_url or not dst_url.startswith(("postgres://", "postgresql://")):
        raise MarketEventsDbError(
            "migrate-to-postgres requires MARKET_EVENTS_DB_URL "
            "(postgresql://trading:pass@localhost:5432/trading_ai)",
        )

    db_name = (urlparse(dst_url).path or "/trading_ai").lstrip("/") or "trading_ai"
    lines = [
        "MARKET EVENTS MIGRATE SQLite → PostgreSQL",
        "",
        f"source_sqlite: {src_path}",
        f"target_database: {db_name}",
        f"target_host: {dst_url.split('@')[-1] if '@' in dst_url else dst_url}",
        "",
        f"{'TABLE':<42} {'SQLITE':>8} {'PG':>8} {'MATCH':>6} {'CHK':>6}",
        "-" * 72,
    ]

    mismatches: list[str] = []

    if not src_path.exists():
        raise MarketEventsDbError(f"SQLite source not found: {src_path}")

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
                        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) {conflict}",
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

    status = "SUCCESS" if not mismatches else "PARTIAL"
    lines.extend([
        "",
        f"status: {status}",
        f"completed_at: {datetime.now(timezone.utc).isoformat()}",
        f"mismatches: {len(mismatches)}",
    ])
    if mismatches:
        lines.append("")
        for m in mismatches:
            lines.append(f"  ! {m}")
    lines.extend([
        "",
        "SQLite source file preserved (not deleted).",
        f"Set MARKET_EVENTS_DB_BACKEND=postgresql and MARKET_EVENTS_DB_URL to use {db_name}.",
    ])
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
    """Compare insert/select/update/join throughput SQLite vs PostgreSQL."""
    cfg = resolve_market_events_db_config()
    src_path = sqlite_path or cfg.sqlite_path
    dst_url = pg_url or (cfg.url if cfg.backend == "postgresql" else None)

    results: dict[str, dict[str, float]] = {}

    def _bench(label: str, url: str | None, path: Path | None) -> None:
        timings: dict[str, float] = {}
        ctx = market_events_connection(url=url) if url else market_events_connection(db_path=path)
        with ctx as conn:
            apply_migrations(conn)
            if connection_is_postgres(conn):
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS _bench_probe (
                      id BIGSERIAL PRIMARY KEY, v TEXT, n INTEGER
                    )
                    """,
                )
            else:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS _bench_probe (
                      id INTEGER PRIMARY KEY, v TEXT, n INTEGER
                    )
                    """,
                )
            conn.execute("DELETE FROM _bench_probe")
            conn.commit()

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
                conn.execute("SELECT COUNT(*) AS n FROM _bench_probe").fetchone()
            timings["select_per_sec"] = round(n / max(time.perf_counter() - t0, 1e-6), 1)

            t0 = time.perf_counter()
            for i in range(n):
                conn.execute(
                    "UPDATE _bench_probe SET n = ? WHERE v = ?",
                    (i + 1, f"row-{i}"),
                )
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

    lines = ["MARKET DB BENCHMARK", "", f"operations: {n} each", ""]

    if src_path.exists():
        ensure_wal_enabled(src_path)
        _bench("sqlite", None, src_path)
    else:
        lines.append("sqlite: skipped (no source file)")

    pg_target = dst_url if dst_url and _is_postgres(dst_url) else None
    if pg_target:
        try:
            _bench("postgresql", pg_target, None)
        except Exception as exc:
            lines.append(f"postgresql: error ({exc})")
    elif cfg.backend == "postgresql":
        try:
            _bench("postgresql", cfg.url, None)
        except Exception as exc:
            lines.append(f"postgresql: error ({exc})")
    else:
        lines.append("postgresql: skipped (MARKET_EVENTS_DB_URL not set)")

    lines.append("")
    if results:
        for label, timings in results.items():
            lines.append(f"[{label}]")
            for k, v in timings.items():
                lines.append(f"  {k}: {v}")
            lines.append("")
    else:
        lines.append("(no benchmarks run)")

    return "\n".join(lines)


def _is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def backup_filename() -> str:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    return f"{ts}.sql.gz"


def _pg_env(parsed) -> dict[str, str]:
    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return env


def db_backup(cfg: MarketEventsDbConfig | None = None, dest: Path | None = None) -> str:
    cfg = cfg or resolve_market_events_db_config()
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    if cfg.backend == "sqlite":
        ts = datetime.now().strftime("%Y-%m-%d_%H%M")
        src = cfg.sqlite_path
        dst = dest or BACKUPS_DIR / f"market_events_{ts}.db.gz"
        if not src.exists():
            return f"SQLite backup skipped: {src} not found"
        with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        for ext in ("-wal", "-shm"):
            side = Path(f"{src}{ext}")
            if side.exists():
                with open(side, "rb") as f_in, gzip.open(Path(f"{dst}{ext}.gz"), "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
        return f"SQLite backup: {dst} ({dst.stat().st_size / 1024:.1f} KB)"

    parsed = urlparse(cfg.url)
    dbname = cfg.database_name
    dst = dest or BACKUPS_DIR / backup_filename()
    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "trading",
        "-d", dbname,
        "--no-owner",
        "--no-acl",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_pg_env(parsed),
        )
        assert proc.stdout is not None
        with gzip.open(dst, "wb") as gz:
            gz.writelines(proc.stdout)
        err = proc.stderr.read().decode() if proc.stderr else ""
        rc = proc.wait()
        if rc != 0:
            if dst.exists():
                dst.unlink()
            return f"PostgreSQL backup FAILED (exit {rc}): {err.strip()}"
        size_kb = dst.stat().st_size / 1024
        return f"PostgreSQL backup: {dst} ({size_kb:.1f} KB, database={dbname})"
    except FileNotFoundError:
        return "PostgreSQL backup FAILED: pg_dump not found in PATH"


def _latest_backup() -> Path | None:
    if not BACKUPS_DIR.exists():
        return None
    candidates = sorted(
        BACKUPS_DIR.glob("*.sql.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def db_restore(
    *,
    archive: Path | None = None,
    cfg: MarketEventsDbConfig | None = None,
) -> str:
    """Restore PostgreSQL from gzip SQL dump in backups/."""
    cfg = cfg or resolve_market_events_db_config()
    if cfg.backend != "postgresql":
        raise MarketEventsDbError("market-db-restore requires MARKET_EVENTS_DB_BACKEND=postgresql")

    path = archive or _latest_backup()
    if not path or not path.exists():
        raise MarketEventsDbError(
            f"No backup found in {BACKUPS_DIR}. Pass --file or run market-db-backup first.",
        )

    parsed = urlparse(cfg.url)
    dbname = cfg.database_name
    psql_cmd = [
        "psql",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "trading",
        "-d", dbname,
        "-v", "ON_ERROR_STOP=1",
    ]
    lines = [
        "MARKET EVENTS DB RESTORE",
        "",
        f"archive: {path}",
        f"database: {dbname}",
        "",
    ]
    try:
        with gzip.open(path, "rb") as gz:
            proc = subprocess.run(
                psql_cmd,
                stdin=gz,
                capture_output=True,
                env=_pg_env(parsed),
            )
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")[-2000:]
            lines.append(f"status: FAILED (exit {proc.returncode})")
            lines.append(err)
        else:
            lines.append("status: SUCCESS")
            with market_events_connection(url=cfg.url) as conn:
                lines.append(f"schema_version: {_schema_version(conn)}")
    except FileNotFoundError:
        lines.append("status: FAILED — psql not found in PATH")

    return "\n".join(lines)
