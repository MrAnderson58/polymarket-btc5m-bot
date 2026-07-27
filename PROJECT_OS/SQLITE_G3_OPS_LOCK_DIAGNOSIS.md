# SQLite lock diagnosis — `market_events_g3_ops_state` (Phase 7)

**Scope:** Investigation only — no behavior changes in this task.

## Symptom

Logs and `doctor` may show:

```text
database is locked
INSERT INTO market_events_g3_ops_state (key, value, updated_at) ...
```

## Writers on `data/market_events.db`

| Process | Table(s) | Approx frequency |
|---------|----------|------------------|
| `shock-paper core` | `market_events_g3_ops_state`, near-miss, shadow, paper runs | ~1 Hz + heartbeat ~60s |
| `shock-paper tradfi` | Same (shared file) | ~1 Hz |
| `dashboard-api` | Mostly read-only | Rare |
| CLI / status | Read-only or short writes | Ad hoc |

`set_g3_ops_state` (`health_g3.py`) is called from:

- `write_system_heartbeat` — **3 upserts/cycle** (`system_heartbeat`, `system_heartbeat_writer`, `system_heartbeat_ts`)
- G3 live recorder (when running) — health keys
- Status readers should **not** write (S2.2)

## Why `g3_ops_state` collides

1. **Two shock-paper processes** hold one SQLite file and each write heartbeat keys every second (after commit retry).
2. **Implicit transactions:** Each `execute` + frequent `commit()` on the long-lived connection extends write lock windows.
3. **Not WAL-disabled:** `PRAGMA journal_mode` on live DB is **wal** (good for readers).
4. **`busy_timeout`:** G0.5 manager sets **10000 ms** on new connections; raw `PRAGMA busy_timeout` on existing handles may read **5000 ms** depending on open path.
5. **Not nested BEGIN** — contention is **multi-writer + many small commits**, not nested transactions.

## Analytics impact

- **Read-only** performance queries (`mode=ro` or readonly connection) are **unaffected** by writer locks in WAL mode in normal conditions.
- Lock failures on **writers** can skip shadow/near-miss flush for one cycle but should not corrupt `paper_strategy_runs` (same connection as paper updates).
- **`g3_ops_state` failures** affect heartbeat visibility in `doctor`, not trade PnL columns.

## Recommended fixes (not applied here)

Already shipped (prior commit `c232db6`):

- Shadow batch + retry; near-miss batch; `retry_on_db_locked(conn.commit)` in paper loop.

Further options:

1. **Single shock-paper writer** per DB (stop duplicate core PIDs).
2. **Debounce g3 ops:** one JSON blob per heartbeat instead of 3 upserts.
3. **Dedicated writer thread / queue** for ops state (optional).
4. **Raise `busy_timeout`** consistently on all connection opens.

See also `PROJECT_OS/SQLITE_LOCK_ANALYSIS.md`.
