# Phase E.5.3.2 — SQLite Concurrent Writer Hardening

Ensures parallel collectors (shock-paper core/tradfi, observe, ai-worker, dashboard) do not hit `database is locked`.

## Connection pragmas (every open)

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;
PRAGMA foreign_keys=ON;
```

Applied in `bot/research/market_events/db.py` → `apply_sqlite_pragmas()`.

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

After startup, processes run in parallel under WAL.

## Tests

```bash
pytest tests/test_sqlite_concurrent_startup.py -q
```

Simulates concurrent core + tradfi + observe startup with zero lock errors.
