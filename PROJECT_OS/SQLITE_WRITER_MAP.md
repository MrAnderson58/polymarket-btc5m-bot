# SQLite Writer Map — Final Elimination

**Date:** 2026-07-27  
**Goal:** `SQLite.......... OK` with **0 lock events / 300s** under full supervisor load.  
**Scope:** Ops write path only — no trading / exit / Performance math changes.

---

## Live measurement (2026-07-27 ~21:12)

Started missing writers (g3, learning, news-intel, event-engine, multi-source) alongside dual shock-paper + ai + dashboard.

`doctor`: **SQLite FAIL — 30 lock event(s) in 300s**

### PID → table → locks (≈120s window)

| PID | Service | Locks | Top table | Notes |
|-----|---------|------:|-----------|-------|
| 28708 | shock-paper-core | **10** | `market_events_g3_ops_state` | old process (no `ME_SERVICE_NAME`) |
| 28736 | shock-paper-tradfi | **8** | `market_events_g3_ops_state` | same |
| 11472 | news-intel | **6** | `market_daily_briefs` | fat INSERT + retry wait ~5–10s |
| 11500 | multi-source | **6** | `market_source_health` | batch write colliding at startup |
| 11467 | learning | 0 | learning ops_state | commits OK |
| 11439 | g3-live | 0 | g3_ops | quiet |
| 11497 | event-engine | 0 | — | quiet |
| — | observe | — | — | **cannot run** (0 instruments) |
| — | telegram | — | — | exited after start |

**Lock tables:** `g3_ops_state` 18 · `market_daily_briefs` 6 · `market_source_health` 6

### Verdict: what is NOT heartbeat-only

~1 lock/s historically (290/300s) comes from:

1. **Write storms** when intel workers open (news brief + multi-source health) collide with **dual shock-paper** upserts on `g3_ops_state`.
2. **Retry inflation** (pre-fix): up to **9 lock events per contended call** → 290 events ≈ ~32 real collisions.
3. **`observe` @ 1Hz** (when instruments exist) would add steady ~1/s pressure — currently stopped (empty universe).

### Updated after paper restart (21:23–21:24)

New PIDs: core=`21093` tradfi=`21483` news=`21514`

| PID | Service | Locks (recent) | Top table |
|-----|---------|---------------:|-----------|
| 11467 | **learning** | high | `market_events_paper_trades_s42` |
| 21093 | shock-paper-core | high | `market_events_universe_log` (startup), `g3_ops_state`, **shadow** |
| 21514 | news-intel | high | `market_daily_briefs` |
| 21483 | shock-paper-tradfi | low after restart | g3_ops |
| 11500 | multi-source | medium at cycle | `market_source_health` |

**Three-way collision (sustained):**  
`learning` (S42 paper inserts) × `news-intel` (briefs) × `shock-paper` (shadow / universe_log / g3_ops).

This pattern, plus pre-fix **×9 retry logging**, explains **~290 lock events / 300s**. Not heartbeat alone.

---

## Static writer map

| Service | Cadence | Hot LIVE tables | Risk |
|---------|---------|-----------------|------|
| shock-paper-core/tradfi | ~1s loop; HB 20s | g3_ops, near_miss, shadow, paper_* | HIGH (dual) |
| observe | ~1s N× INSERT | price_observations | CRITICAL if instruments>0 |
| news-intel | 60s / 30m / 2h | news_feed, **daily_briefs** | HIGH at brief/agg |
| multi-source | ~15m | news_feed, **source_health**, macro… | HIGH at cycle |
| g3-live | ~60s | snapshots/trends/signals/ops | MED |
| learning | ~60s | learning_s40, paper_s42 | LOW–MED |
| event-engine | ~15m | market_intel_events | LOW |
| ai-worker | on jobs | analysis_* | LOW if idle skip |
| dashboard | HTTP | **readonly GET** | LOW |
| telegram | on message | ME traces | LOW |

---

## Commands

```bash
python -m bot.research.market_events status
python -m bot.research.market_events doctor
python -m bot.research.market_events sqlite-writer-map
python -m bot.research.market_events sqlite-contention-report
```

Trace: `logs/me-sqlite-write-trace.jsonl` (`pid`, `service`, `table`, `kind`, `locked`).

---

## Fixes in this pass

| Fix | Why |
|-----|-----|
| `ME_SERVICE_NAME` + PID→service map in report | Attribute locks without guessing |
| Retry lock log = first+final only | Stop ~9× inflation |
| Observe: 1 commit/cycle | Cut dual commits |
| Dashboard GET → readonly | Remove migrate-per-request writers |
| AI idle → no DB | Remove idle writers |
| News brief: **RO load + short write** | Stop holding write conn during brief build |
| Persist commits to write-trace | Cross-process commit/s |

---

## Acceptance

Under full load (all listed services):

```text
SQLite.......... OK
Recent lock warnings: 0
sqlite-contention-report → Lock events: 0
```

### Ops notes

1. **Restart shock-paper-core/tradfi** so they pick up debounce + service name + retry fix.
2. `observe` needs `instrument-discover` / non-empty tradfi-observe universe before it can run.
3. Avoid simultaneous cold-start of news-intel + multi-source + dual paper without stagger if locks spike.
