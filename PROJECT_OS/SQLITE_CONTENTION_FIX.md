# SQLite Lock Contention — Eliminate Write Collision

**Date:** 2026-07-27  
**Scope:** Ops write path only — no strategy / exit / detector changes.

## Phase 1–3 — Measurements (pre-fix)

Dual `shock-paper` writers on `data/market_events.db`:

```
shock-paper-core  ──┐
shock-paper-tradfi ─┼─► market_events_g3_ops_state  (3 upserts / cycle ≈ 1 Hz each)
                    ├─► COMMIT ×3 / cycle / process
                    └─► near_miss / shadow (≤1 / heartbeat_sec)
```

Sample (last ~2000 COMMIT log lines per process):

| Source | g3_ops share | Notes |
|--------|-------------:|-------|
| core | ~91% (1194/1309) | heartbeat every poll |
| tradfi | ~98% (1770/1813) | empty universe still heartbeats |

`system_heartbeat*` keys refreshed every ~1s (well below 180s stale threshold).

## Phase 4 — Chosen fix (smallest)

1. **Debounce** `write_system_heartbeat` to 20s / process (`ME_SYSTEM_HEARTBEAT_MIN_INTERVAL_SEC`).
2. **Batch** the 3 ops keys in one `executemany`.
3. **Single commit** per paper cycle (was 3).
4. **Skip empty commits** (`dirty` flag on G0.5 connection).
5. **Quiet COMMIT INFO spam** (log COMMIT only when slow or `ME_SQLITE_TRACE_COMMITS=1`).
6. **Instrument** writes → ring buffer + `logs/me-sqlite-write-trace.jsonl` for locks/slow ops; CLI `sqlite-contention-report`.

Not chosen: Postgres migration, dedicated writer thread, storage redesign.

## Phase 5 — Files

- `sqlite_manager_g05.py` — write trace, dirty commits, quieter logs
- `heartbeat_diagnostics_g352.py` — debounce + batch
- `paper_runner.py` — one commit / cycle
- `runtime_health.py` — timestamp-aware lock lookback; doctor text
- `__main__.py` — `sqlite-contention-report`

## Acceptance

```bash
python -m bot.research.market_events doctor
# SQLite.......... OK  (Recent lock warnings: 0)

python -m bot.research.market_events sqlite-contention-report
```

After 30+ minutes healthy: 0 lock events in lookback window.
