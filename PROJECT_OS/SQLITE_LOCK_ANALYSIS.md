# SQLite Lock Analysis — Adaptive Shadow Pipeline

## Writer map (`data/market_events.db`)

Live processes observed holding the file open:

| Process | Role | Write cadence |
|---------|------|---------------|
| `shock-paper core` | Main shock/paper loop | Every poll (~1s) + heartbeat (~60s) |
| `shock-paper tradfi` | Second paper loop (often empty universe) | Same cadence on shared DB |
| `dashboard-api` | Read-mostly API | Occasional status touches |

Other registered writers (when running): `ai-worker`, `g3-run`, `observe`, news/multi-source collectors.

### Tables touched each `shock-paper` cycle

| Table | When | Approx frequency |
|-------|------|------------------|
| `market_events` / snapshots / pending / paper | Only on real shock accept | Rare (baseline thresholds high) |
| `market_events_g3_ops_state` (heartbeat keys) | Every cycle | ~1 Hz per process |
| `market_events_near_miss_summaries` | Heartbeat flush | ~60 rows / 60s / process |
| `market_events_shadow` | Buffered → heartbeat (or buffer≥400) | Batched, was 0 before restart+fix |
| scheduler / outcome side-effects | Heartbeat `scheduler_tick` | ~1/min |

## Why `database is locked` appeared

Primary causes (combined):

1. **Multiple writers** — core + tradfi both open the same SQLite file and write heartbeat/`g3_ops_state` every second.
2. **Row-by-row inserts** — adaptive shadow (and near-miss) used many individual `INSERT`s inside the long-lived paper connection, holding the write lock longer under contention.
3. **Shadow mid-cycle writes** — adaptive accepts could enqueue writes every poll; with lower thresholds that spikes I/O vs baseline.
4. **Commit without retry on the hot path** — cycle `conn.commit()` was not wrapped in `retry_on_db_locked`, so a busy peer for > `busy_timeout` surfaced as `cycle error: database is locked`.
5. **Not nested transactions** — no deliberate nested `BEGIN`; the pain was concurrent processes + many small writes + bare commit.

Side note: the live core PID at diagnosis was still running **pre-adaptive** code, so `market_events_shadow` stayed empty even when locks were rare — restart after deploy is required for rows to appear.

## What we fixed

Without changing detector thresholds or paper/live activation logic:

1. **Shadow buffer + batch flush** (`adaptive_shock_shadow.py`)
   - Evaluate every cycle in memory (metrics unchanged).
   - Persist via `executemany` + `retry_on_db_locked`.
   - Flush on heartbeat (with near-miss) or when buffer ≥ 400.
   - On lock, keep buffer and retry next flush (do not abort the paper cycle).

2. **Near-miss batching** (`near_miss_shadow.py`) — same `executemany` + retry pattern.

3. **Heartbeat ops upsert retry** (`health_g3.set_g3_ops_state`) — `execute_with_retry`.

4. **Paper loop commits** (`paper_runner.py`) — `retry_on_db_locked(conn.commit)` after cycle / heartbeat / flush; rollback on cycle error.

5. **`TracedConnectionG05.executemany`** — traced batched writes through the G0.5 manager.

## Why locks should not recur on the shadow path

- Shadow no longer sprays per-row inserts every second into a contended DB.
- Batches are one SQLite statement with exponential backoff (same schedule as G0.5: up to ~10s).
- Failed flushes are deferred, not escalated into `cycle error`.
- Heartbeat writers share the same retry primitive, so brief cross-process collisions wait instead of failing the loop.

Residual risk remains if many heavy writers (g3 + AI + dual paper) all spike at once; the adaptive shadow path itself is no longer a lock amplifier.

## Verify

```bash
# after restarting shock-paper-core on the new commit
python -m bot.research.market_events shadow-report --days 1
# expect non-zero checked/accepted for adaptive_v1 once prices move

sqlite3 data/market_events.db \
  "SELECT COUNT(*), MAX(created_at) FROM market_events_shadow;"
```
