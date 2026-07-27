# SQLite Final Report — Investigation Closed

**Date:** 2026-07-27  
**Branch:** `develop-terminal` @ `17b3457` (origin)  
**Status:** **CLOSED — acceptance not met; unfinished write-path optimizations abandoned (not pushed)**

---

## Decision (product)

1. **Stop** further SQLite micro-optimizations in this cycle.
2. **Do not push** unfinished stabilization / write-path batching from the working tree.
3. **Return** to the stable tip (`develop-terminal`) and **prioritize collecting trading data**.
4. **Defer architectural refactor** (e.g. dedicated SQLite writer process) until **real collisions cause data loss or missed trades**. Contention alone, with retries succeeding and no proven loss, is not enough to justify that refactor now.

---

## What we measured

Instrumentation: `logs/me-sqlite-write-trace.jsonl`, `sqlite-contention-report`, doctor lock window.

| Finding | Evidence |
|---------|----------|
| Multiple writers share one live DB | learning, dual shock-paper, g3, multi-source, news-intel, event-engine, … |
| Hot tables | `g3_ops_state`, `shadow`, `trade_features_s55`, `paper_trades_s42`, `market_source_health`, `market_intel_events` |
| Doctor “~290 locks/300s” was inflated | busy-retries counted as separate events (up to ~9× per collision) |
| Real collisions still exist after counting fix | Run A: **86 incidents / 1800s**, doctor **6 / 300s**; Run B: doctor **13 / 300s**, ~**11** on new PIDs |
| Long dirty windows | g3-live often **~1000+ rows/commit**, avg commit wall **~10s**, max tens of seconds |
| Known writers (user list) confirmed | learning→`paper_trades_s42`; news→`market_daily_briefs`; multi-source→`market_source_health`; shock-paper→`shadow` + `g3_ops_state` |

### Phase 1 — transaction metrics (representative)

**Pre-stabilization hotspot (~30 min):**

| Service | c/min | avg_ms | max_ms | avg_rows |
|---------|------:|-------:|-------:|---------:|
| g3-live | ~1.1 | 1683 | 55407 | 1681 |
| shock-paper-core | ~2.4 | 499 | 32436 | 24 |
| learning | ~4.0 | 17 | 1502 | 19 |

**Run A (round-1 local opts, 1800s):** 86 lock incidents; doctor FAIL (6/300s).  
**Run B (round-2 local opts after restart):** doctor FAIL (13/300s); collisions remain (e.g. `shadow` ↔ `trade_features_s55`).

Artifacts: `/tmp/sqlite_final_doctor_r2.txt`, `/tmp/sqlite_final_contention_r2.txt`.

---

## What we tried (local only — not shipped)

Write-path changes only (no trading / paper strategy logic): shorter commits, shadow chunk flush, heartbeat debounce, learning phase splits, mid-cycle g3 commits, incident clustering for doctor.

**Result:** metrics improved in places; **near-zero real lock incidents under full load was not achieved.** Code was **reverted to stable HEAD**; not committed / not pushed.

---

## Current operating posture

| Item | State |
|------|--------|
| Runtime code | Stable `develop-terminal` tip (SQLite “final elim” WIP discarded) |
| Live feed helpers | May remain local (Bybit/OKX proxy scrub) for data collection — separate from SQLite WIP |
| Doctor SQLite | May still show lock incidents under full load — **expected** until architecture change |
| Data integrity gate | Watch for **lost writes** or **missed paper opens/closes**; that is the trigger for writer-process refactor |

Companion map (investigation notes): `PROJECT_OS/SQLITE_WRITER_MAP.md` (if present).

---

## When to reopen architecture

Reopen only if any of:

- Confirmed **lost** INSERT/UPDATE (retry exhausted, row missing)
- Confirmed **skipped** paper entry/exit due to lock (not mere delayed retry)
- Sustained inability to run required collectors without disabling writers

Then prefer: **dedicated writer process** / queue (or stronger DB separation), not more ad-hoc commit splitting.

---

## Commands (ops / diagnose)

```bash
python -m bot.research.market_events doctor
ME_SQLITE_CONTENTION_WINDOW_SEC=1800 python -m bot.research.market_events sqlite-contention-report
python -m bot.research.market_events status
```

---

## Next focus

**Collect trading data** on stable runtime (shock-paper + learning + required intel).  
SQLite contention work is **parked**.
