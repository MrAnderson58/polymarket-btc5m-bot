# Paper Trading Lifecycle Audit

**Date:** 2026-07-27  
**Scope:** Why `performance` reports 0 completed trades. Analytics-only investigation — no trading-logic changes.

**DB:** `data/market_events.db`  
**Related:** `GATE_ANALYSIS.md`, `BUG_window_start_ts.md`, `PATCH_window_start_ts.md`

---

## Phase 1 — Lifecycle (code path)

| Step | Module | What happens | Persist target |
|------|--------|--------------|----------------|
| 1. Price poll | `paper_runner.run_once` → `BybitPriceFeed.poll_universe` | Tick history for universe symbols | In-memory feed state |
| 2. Shock detect | `scan_universe_for_shocks` | Absolute/relative return gates | — |
| 3. Signal stored | `_persist_event` | INSERT shock row | `market_events` (`phase=SHOCK_DETECTED`) |
| 4. Snapshot | `persist_snapshot` | Detection price snapshot | `market_event_snapshots` |
| 5. Context link | `link_event_context` → `_link_polymarket_state` | Attach Polymarket checks from `trades.db` | context tables |
| 6. Pending watch | `create_pending_shock` | Start ≤900s reversal monitor | `market_events_pending_shocks` (`MONITORING_REVERSAL`); updates `market_events.phase` |
| 7. Reversal eval | `process_pending_shock` → `evaluate_all_reversals` (R1–R5) | Confirm reclaim / velocity / etc. | `market_event_lifecycle_decisions` |
| 8. Paper entry | `_open_paper_runs` | One row per exit variant EXIT_A…E | `paper_strategy_runs` (`exit_ts=NULL`) |
| 9. Exit eval | `_process_open_positions` → `process_exit_tick` | TP / stop / BE / trail | In-memory `PaperPosition` |
| 10. Trade closed | UPDATE on close | Fill exit + PnL % | `paper_strategy_runs.exit_ts`, `net_return`, … |
| 11. Performance | `performance.load_completed_trades` | Read-only analytics | `WHERE exit_ts IS NOT NULL` |

**There is no separate “performance table.”** Performance reads closed `paper_strategy_runs`.

Exit / size notes (unchanged): no USD size column; analytics assumes `$100` notional × `net_return%`. No cancelled status on paper runs; expiry is `pending_shocks.phase=EXPIRED_NO_REVERSAL`.

---

## Phase 2 — Current DB counts (2026-07-27)

| Metric | Count |
|--------|------:|
| `market_events` (shocks) | **12** |
| … phase `SHOCK_DETECTED` | **12** |
| … any later phase | **0** |
| `market_events_pending_shocks` | **0** |
| `market_event_lifecycle_decisions` | **0** |
| `paper_strategy_runs` total | **0** |
| … open (`exit_ts IS NULL`) | **0** |
| … closed (`exit_ts NOT NULL`) | **0** |
| Cancelled (N/A — no column) | **0** |
| Expired pending | **0** |
| S42 `market_events_paper_trades_s42` | **0** |

Last shock: DOGE id=12 @ 2026-07-24 06:40 UTC. No events since the linker fix.

---

## Phase 3 — Why closed trades = 0 (root cause)

### Exact cause (historical 12/12 detections)

**Cycle aborted in context-link before pending / paper.**

Evidence (paired 1:1 in `logs/me-shock-paper-core.log`):

```
cycle error: no such column: window_start_ts
events_detected=N+1
pending_reversals=0
paper_runs_open=0
```

- **12** such errors; **12** `market_events`; **0** pending; **0** paper.
- Failure site: `link_event_context` → `_link_polymarket_state` selecting `market_checks.window_start_ts` on `trades.db`.
- Live `market_checks` only has `checked_at` (never had `window_start_ts`).
- Exception is uncaught in `run_once` → `cycle error` → **rollback** of that cycle’s remaining work → `create_pending_shock` / `_open_paper_runs` **never run**.
- In-memory `events_detected` still increments (hence heartbeats show detections while `pending_reversals` stays 0).

**Not the cause:** exit logic broken, exit worker missing, wrong performance table, or missing leverage/size. Exit never ran because **no open rows exist**.

### Fix already in git (not the current live blocker)

Commit **`7349037`** (2026-07-26): linker uses `checked_at`. On-disk code is correct. No further `window_start_ts` cycle errors after event 12.

### Current live blocker (why still 0 after the fix)

Runner is alive (heartbeat age ~0s, writer `shock-paper`) but **cannot fetch prices**:

```
fetch_ok=0
fetch_failed=102390+
ProxyError … HTTPSConnection(host='127.0.0.1', port=65470) Connection refused
price poll failed … bybit miss + okx miss
```

Without ticks: no new shocks → no pending → no paper → Performance stays 0.  
The 12 historical rows stay frozen at `SHOCK_DETECTED` (pipeline does not retroactively attach pending).

---

## Phase 4 — Lifecycle diagram (where it stops)

```
Signal (shock detect)
   ↓  ✓  12 rows in market_events
Paper Entry prep (snapshot)
   ↓  ✓  12 market_event_snapshots
Context link (Polymarket)
   ↓  ✗  STOPPED HERE (window_start_ts) for all 12 historical events
Pending / Open Position
   ↓  ✗  never reached (0 pending, 0 paper_strategy_runs)
Exit Logic
   ↓  ✗  never reached
Trade Closed
   ↓  ✗  never reached
Performance (exit_ts IS NOT NULL)
   →  correctly reports 0
```

**After patch, pipeline stop moved to:**

```
Price poll
   ↓  ✗  STOPPED HERE now (proxy / fetch_ok=0)
Signal …
```

---

## Phase 5 — Smallest fix (no redesign)

Do **not** change strategy, exits, or thresholds.

1. **Already done (code):** keep `7349037` linker (`checked_at`). Do not reintroduce `window_start_ts` on `market_checks`.
2. **Ops (required now):** restore price connectivity for `shock-paper-core`:
   - Clear bad `HTTP(S)_PROXY` pointing at `127.0.0.1:65470` (or fix that local proxy).
   - Restart `shock-paper-core` / `shock-paper-tradfi` so the process uses a clean env + current code.
   - Confirm heartbeat shows `fetch_ok > 0` and no `ProxyError` spam.
3. **Do not** backfill the 12 stuck events unless explicitly requested — they are past the 900s monitor window; next *new* detection is enough to prove the lifecycle and feed Performance.
4. **Optional hardening (later, tiny):** wrap `link_event_context` in try/except so a context-DB failure cannot abort pending/paper (context is additive). Not required if linker stays correct and feed is healthy.

### Success criteria

- New shock → `pending_shocks` row → (on confirm) `paper_strategy_runs` open → exit UPDATE → `python -m bot.research.market_events performance` shows trades > 0.
