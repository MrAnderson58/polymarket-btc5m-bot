# Phase E.5.3.2 — SQLite Concurrent Writer Hardening

Ensures parallel collectors (shock-paper core/tradfi, observe, ai-worker, dashboard) do not hit `database is locked`.

## WAL mode (once, before workers)

`PRAGMA journal_mode=WAL` must **not** run on every `connect()` — if another process already holds the DB, the mode switch fails with `database is locked`.

Use `ensure_wal_enabled()` instead — called **once** from:

- `market-event-migrate`
- `start-all` (before spawning workers)

## Per-connection pragmas

Applied in `apply_sqlite_pragmas()` on every open:

```sql
PRAGMA synchronous=NORMAL;   -- only when journal_mode is already WAL
PRAGMA busy_timeout=10000;
PRAGMA foreign_keys=ON;
```

## Diagnostics

```bash
python -m bot.research.market_events db-info
```

Shows `journal_mode`, `busy_timeout`, `foreign_keys`, `page_size`, `cache_size`, `sqlite_version`, `database_list`, and WAL/SHM file presence.

## Lock retry

All write helpers use `retry_on_db_locked()` with exponential backoff:

50 → 100 → 200 → 400 → 800 → 1600 → 3200 → 3650 ms (total ~10s)

- `execute_with_retry(conn, sql, params)`
- `insert_returning_id(conn, sql, params)`
- `with_retry_transaction(conn)` — `BEGIN IMMEDIATE` + commit retry
- `market_events_connection()` — commit retry on exit

## Startup serialization

File lock: `data/market_events_startup.lock`

Used at startup by:

- `shock-paper-run` (migrations + universe log + restore state)
- `observe-run` (migrations + observe universe log)
- `ai-worker-run` (migrations once)

After WAL is enabled and startup completes, processes run in parallel.

## Mac Mini deploy

```bash
python -m bot.research.market_events market-event-migrate   # enables WAL + schema
python -m bot.research.market_events db-info                # verify journal_mode: wal
python -m bot.research.market_events start-all
```

## Tests

```bash
pytest tests/test_sqlite_concurrent_startup.py -q
```

Simulates concurrent core + tradfi + observe startup with zero lock errors.
