# SQLite Lock Count Investigation (post-rollback)

**Date:** 2026-07-27 ~23:40 local  
**Scope:** Why doctor still shows ~hundreds of lock events/300s on stable `develop-terminal`  
**Action:** Investigation only — **no code changes**

---

## Short answers

| Question | Answer |
|----------|--------|
| Do retries count as separate events? | **Yes.** Each busy attempt logs a lock event; often **twice** per attempt (wrapper + `retry_on_db_locked`). Up to **~9 attempts** (~10s backoff). |
| New locks after restart vs old records? | Doctor uses a **rolling 300s** window on the JSONL trace. Counts after ~23:05 restart are **from current PIDs only** (not leftover old PIDs). The file still contains older history, but the 300s window does not. |
| How many **real** collisions since last start? | **~235** (cluster: same `pid`+`table` within 2s). Raw lock **events**: **1025** since restart (~**4.4×** inflation). |
| Lost writes / skipped paper trades? | **No evidence of permanent lost paper opens.** **Yes:** several learning cycles **aborted** after lock retries exhausted during **MFE/MAE tick** → that cycle’s remaining ticks skipped until the next minute. Shadow rows still accumulating; no post-restart `flush deferred` with ISO stamp found. |

---

## Why ~296 (and similar) on doctor

### Mechanism (stable tip)

1. `doctor` → `_sqlite_lock_recent()` → `get_recent_lock_events(lookback=300)`  
2. Counts every JSONL/in-memory event with `locked=True`  
3. Label: **`N lock event(s) in 300s`** — not “incidents”

Source path:

- `runtime_health.py`: prefers trace count  
- `db.retry_on_db_locked`: on **every** failed attempt calls `maybe_log_database_locked`  
- `sqlite_manager_g05` `execute`/`executemany`: on lock also calls `maybe_log_database_locked` **before re-raise**  
- Retry budget: **8 sleeps**, **9 attempts** total (`50ms …` up to ~10s)

So one hard collision can produce on the order of **~2 × attempts ≈ up to ~18** doctor “events”.

Observed burst sizes since restart: mostly **2**, sometimes **10** or **100** (large learning feature storms).

### Measured on this machine

| Metric | Value |
|--------|------:|
| Restart (min supervisor pid mtime) | **2026-07-27 23:05:52** |
| Current PIDs | core=387, tradfi=389, learning=449, … |
| Raw lock events since restart (current PIDs) | **1025** |
| Real incidents (2s cluster, pid+table) | **235** |
| Inflation | **~4.4×** |
| Peak raw events in any rolling **300s** since restart | **314** (from **23:23:45**) ← matches user “~296” |
| Peak **incidents** in any 300s | **48** |
| Doctor sample during investigation | 62 → 166 events/300s (moving window) |
| Last 300s at analysis time | raw **54** / incidents **27** |

**Conclusion:** ~296 is a **peak of retry-inflated lock log lines** in a busy 5-minute window after restart, **not** ~296 distinct DB collisions, and **not** stale pre-restart PIDs in the 300s window.

---

## Who locks (since restart, current PIDs)

### Raw events by PID

| PID | Service | Raw lock events |
|----:|---------|----------------:|
| 449 | learning | 655 |
| 389 | shock-paper-tradfi | 160 |
| 387 | shock-paper-core | 160 |
| 495 | multi-source | 20 |
| 481 | event-engine | 14 |
| 455 / 505 | news-intel / narrative | 8 each |

### Raw vs incidents by table

| Table | Raw events | ~Incidents (2s) |
|-------|----------:|----------------:|
| `market_events_trade_features_s55` | 500 | 5 |
| `market_events_g3_ops_state` | 240 | 120 |
| `market_events_paper_trades_s42` | 95 | 15 |
| `market_events_shadow` | 80 | 40 |
| `market_source_health` / intel / reviews / ops | rest | rest |

`trade_features_s55` dominates **raw** count (retry storms) but few **incidents**. `g3_ops_state` and `shadow` dominate **real** dual-writer collisions.

Near-duplicate pairs with Δt &lt; 20ms: **876** → strong evidence of **double logging** per attempt.

---

## Data integrity (since restart)

| Check | Result |
|-------|--------|
| S42 `created_at >= restart` | **0** new opens |
| S42 OPEN now | **21** (pre-existing; `updated_at` advanced on **21**) |
| S42 closed since restart | **0** |
| S55 features since restart | **100**, all `gate_decision=NEGATIVE_EXPECTANCY`, all `paper_trade_id IS NULL` → **intentional gate denials**, not lock skips of allowed opens |
| Shadow rows `created_at >= restart` | **2500** (writes succeeding) |
| `shadow flush deferred` after restart (ISO-stamped) | **0** found |
| Learning `s40 worker cycle failed` after restart | **≥6** (ISO-associated); cause: **`database is locked` after full retry** inside `tick_open_paper_trades_s42` (MFE/MAE `UPDATE`) |

### Interpretation of learning failures

- Retries **exhausted** (~10s) → exception propagates → **that worker cycle aborts**.  
- Effect: **some open trades may miss one tick’s MFE/MAE update** until the next successful cycle (~1 min).  
- Not the same as “paper trade never opened” or “row deleted.”  
- No evidence in this window that an **allowed** open was dropped solely due to lock (new opens were gate-denied as `NEGATIVE_EXPECTANCY`).

Permanent data loss: **not demonstrated**.  
Transient skip of paper **tick** work: **yes, confirmed** on exhausted lock retries.

---

## Why rollback didn’t make doctor “green”

Stable tip **intentionally** counts raw lock **events**. The unpushed “incident” counter was reverted with the SQLite WIP. Under full multi-writer load, contention continues; doctor will keep FAIL’ing whenever the 300s window contains many retries.

---

## Method

```bash
python -m bot.research.market_events doctor
# Trace: logs/me-sqlite-write-trace.jsonl  (locked=true)
# Cluster: same (pid, table) within 2s = 1 incident
# Restart bound: min mtime of data/market_events_supervisor/*.pid
```

---

## Bottom line

Doctor’s ~296/300s is **mostly retry (+ double-log) inflation** on **real but fewer** post-restart collisions (~tens of incidents per 5 minutes at peak, ~235 since start). Trace window is **fresh** for current processes. **Paper opens** in this window were gate-rejected, not lock-dropped; **tick updates** sometimes **failed a full cycle** after lock timeout — delayed metrics, not proven permanent loss.
