# PROJECT_OS — Live Write Path (`shock-paper-run`)

_Read-only recovery audit. Generated from live PIDs, `lsof`, SQLite counts, process logs, and source. No code or data modified._

## Running processes (this machine)

| PID | Command | Elapsed | Open SQLite |
|-----|---------|---------|-------------|
| **7323** | `python -m bot.research.market_events shock-paper-run --universe core --paper-only` | ~12d | `data/market_events.db` (+ WAL/SHM) |
| **7325** | `… shock-paper-run --universe tradfi-liquid --paper-only` | ~12d | **same** `data/market_events.db` |
| 7329 | `… ai-worker-run` | ~12d | idle (no AI jobs); no shock writes |
| 7334 | `… dashboard-api-serve` | ~12d | read API |

`python -m bot.research.market_events status` confirms:

- Heartbeat: **OK** (`writer=shock-paper`, ~1s ago)
- Last event: **~40h ago**
- DB path: `/Users/andrey/polymarket-bot/polymarket-btc5m-bot/data/market_events.db`
- Logs: `logs/me-shock-paper-core.log`, `logs/me-shock-paper-tradfi.log`

Env of note: `ME_AI_EMBEDDED_IN_PAPER_RUN=false`. No `MARKET_EVENTS_DB_URL` override → default SQLite path.

**Neither process opens `trades.db` or `market_events_research.db` for writes.**  
(`DATABASE_PATH=data/trades.db` in the process env is the Polymarket bot legacy var; shock-paper resolves its own DB via `market_events_connection()`.)

---

## Flow diagram

```
shock-paper-run  (PID 7323 core / 7325 tradfi)
    ↓
__main__.py → paper_runner.run_shock_paper → ShockPaperRunner.run()
    ↓
market_events_connection()
    ↓
SQLite: data/market_events.db   [NO SQLAlchemy — raw sqlite3 + insert_returning_id]
    ↓
┌─────────────────────────────────────────────────────────────┐
│ LIVE NOW (verified appending)                               │
│  • market_events_near_miss_summaries   (heartbeat)          │
│  • market_events_g3_ops_state          (system_heartbeat*)  │
├─────────────────────────────────────────────────────────────┤
│ WIRED but NOT appending now                                 │
│  • market_events                       (detect)             │
│  • market_event_snapshots              (detect)             │
│  • market_event_context                (detect)             │
│  • market_events_pending_shocks        (detect/confirm)     │
│  • market_event_lifecycle_decisions    (detect/confirm)     │
│  • paper_strategy_runs                 (open/exit)          │
│  • alert / AI job / F0–G3 hook tables  (flag-gated)         │
└─────────────────────────────────────────────────────────────┘
    ↓
consumers: status heartbeat · shock-*-report CLIs · dashboard :8765 · near-miss report
```

\*Heartbeat keys: `system_heartbeat`, `system_heartbeat_ts`, `system_heartbeat_writer`.

---

## DB resolution (no SQLAlchemy)

| Priority | Env | Result |
|----------|-----|--------|
| 1 | `MARKET_EVENTS_DB_URL=postgres://…` | PostgreSQL |
| 2 | `MARKET_EVENTS_DB_URL=sqlite:///…` | that file |
| 3 | `MARKET_EVENTS_DATABASE_PATH` | SQLite path |
| 4 | default | `{repo}/data/market_events.db` |

Module: `bot/research/market_events/db.py` + `db_config.py`.  
ORM: **none** on this path (raw SQL strings).

---

## Write catalog (module → SQL → table → consumer)

### 1. Shock event (detect) — **stale**

| Field | Value |
|-------|-------|
| **Module** | `paper_runner.ShockPaperRunner._persist_event` |
| **Table** | `market_events` |
| **SQL** | `INSERT INTO market_events (event_ts, detected_ts, venue, symbol, event_type, direction, phase, trigger_window_seconds, return_pct, velocity, acceleration, volume_zscore, market_return_pct, btc_return_pct, relative_return_pct, classification, confidence, detector_version, detector_triggers_json, dedup_key, raw_metrics_json, created_at, instrument_id, asset_class, session_regime, reference_return_pct, basis_bps, cross_classification) VALUES (…)` |
| **Also** | `UPDATE market_events SET phase = ? WHERE id = ?` via `pending_reversal.py` |
| **Consumer** | `shock-event-report`, lifecycle audits, dashboard, F/G hooks |
| **Live?** | **No** — count **12**, last `created_at` ~40h ago (DOGE). All rows phase=`SHOCK_DETECTED` |

### 2. Event snapshot (detect) — **stale**

| Field | Value |
|-------|-------|
| **Module** | `market_snapshot.persist_snapshot` |
| **Table** | `market_event_snapshots` |
| **SQL** | `INSERT INTO market_event_snapshots (event_id, snapshot_ts, offset_seconds, price, return_from_event, volume, bid, ask, spread_bps, orderbook_imbalance, open_interest, funding, context_json, reference_price, basis_bps, tracking_error_bps) VALUES (…)` |
| **Consumer** | AI context, F51, timeline |
| **Live?** | **No** — 12 rows, mirrors events |

### 3. Event context (detect) — **empty**

| Field | Value |
|-------|-------|
| **Module** | `event_context_linker._insert_context` |
| **Table** | `market_event_context` |
| **SQL** | `INSERT OR IGNORE INTO market_event_context (event_id, context_type, source, source_record_id, context_ts, time_delta_seconds, relevance_score, context_json, created_at) VALUES (…)` |
| **Live?** | **No** — 0 rows |

### 4. Pending reversal (detect / confirm) — **empty**

| Field | Value |
|-------|-------|
| **Module** | `pending_reversal.create_pending_shock` / `process_pending_shock` |
| **Table** | `market_events_pending_shocks` |
| **SQL (create)** | `INSERT OR REPLACE INTO market_events_pending_shocks (event_id, symbol, direction, phase, detected_ts, shock_return_pct, shock_extreme_price, shock_extreme_ts, monitor_until_ts, monitor_horizons_json, created_at, updated_at) VALUES (…)` |
| **SQL (confirm/tick/expire)** | `UPDATE market_events_pending_shocks SET …` |
| **Live?** | **No** — 0 rows; log `pending_reversals=0` |

### 5. Lifecycle decisions — **empty**

| Field | Value |
|-------|-------|
| **Module** | `lifecycle_decisions.persist_reversal_decisions` |
| **Table** | `market_event_lifecycle_decisions` |
| **SQL** | `INSERT INTO market_event_lifecycle_decisions (event_id, decision_ts, stage, status, reason, details_json, created_at) VALUES (…)` |
| **Live?** | **No** — 0 rows |

### 6. Paper strategy runs (open / exit) — **never populated on this DB**

| Field | Value |
|-------|-------|
| **Module** | `paper_runner._open_paper_runs` (INSERT); `_process_open_positions` (UPDATE) |
| **Table** | `paper_strategy_runs` |
| **SQL (open)** | `INSERT OR IGNORE INTO paper_strategy_runs (event_id, strategy_name, strategy_version, reversal_variant, exit_variant, eligibility, rejection_reason, signal_ts, entry_ts, entry_price, initial_stop, breakeven_trigger, trailing_mode, take_profit_mode, fee_bps, slippage_bps, created_at) VALUES (…)` |
| **SQL (exit)** | `UPDATE paper_strategy_runs SET exit_ts=?, exit_price=?, exit_reason=?, gross_return=?, net_return=?, mfe=?, mae=?, … WHERE event_id=? AND reversal_variant=? AND exit_variant=?` |
| **Consumer** | `shock-strategy-report`, digests, dashboard open-runs |
| **Live?** | **No** — **0 rows total**. Requires reversal confirm after detect; historical events never left `SHOCK_DETECTED`. |

> **Not the same as** `market_events_paper_trades_s42` (S40/S42 signal-learning paper). Shock-paper does **not** write S42.

### 7. Near-miss summaries (heartbeat) — **APPENDING NOW**

| Field | Value |
|-------|-------|
| **Module** | `near_miss_shadow.persist_near_miss_snapshots` via `ShockPaperRunner._maybe_heartbeat` |
| **Table** | `market_events_near_miss_summaries` |
| **SQL** | `INSERT INTO market_events_near_miss_summaries (symbol, profile_name, window_sec, direction, session_regime, max_abs_return_pct, current_abs_return_pct, p95_abs_return_pct, p99_abs_return_pct, threshold_pct, max_threshold_reached_pct, max_volume_zscore, max_relative_return_pct, episodes_25pct, episodes_50pct, episodes_75pct, episodes_90pct, period_start, period_end, created_at) VALUES (…)` |
| **When** | Every `ME_HEARTBEAT_SEC` (default **60s**) if in-memory tracker non-empty |
| **Consumer** | `shock-near-miss-report` |
| **Live?** | **Yes** — ~545k rows; **+60 rows in 45s**; ~3360 inserts/hour; `MAX(created_at)` age seconds |

### 8. System heartbeat (every cycle) — **APPENDING / UPSERTING NOW**

| Field | Value |
|-------|-------|
| **Module** | `heartbeat_diagnostics_g352.write_system_heartbeat` → `set_g3_ops_state` |
| **Table** | `market_events_g3_ops_state` |
| **SQL** | `INSERT INTO market_events_g3_ops_state (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at` |
| **Keys** | `system_heartbeat`, `system_heartbeat_ts`, `system_heartbeat_writer="shock-paper"` |
| **Live?** | **Yes** — `updated_at` advances every ~cycle; `status` CLI reports OK |

### 9. Startup-only

| Table | Module | SQL |
|-------|--------|-----|
| `market_events_migrations` | `event_schema.apply_migrations` | migration bookkeeping |
| `market_events_universe_log` | `universe._log_universe` / `select_universe` | `INSERT INTO market_events_universe_log (version_tag, symbols_json, selection_reason, created_at) VALUES (…)` |

Logged at start: core `["BTC","ETH","SOL",…]`; tradfi-liquid **`[]`**.

### 10. Optional / flag-gated (not active writers now)

Enqueue / hooks from detect-exit when flags on (F0, G3, alerts, AI, F72, …). On this host:

- `ME_AI_EMBEDDED_IN_PAPER_RUN=false`
- `ai-worker-run`: `ME_AI_ANALYST_ENABLED=false` → idle
- `market_event_analysis_jobs` / `market_event_ai_analyses` = **0**
- Alert / F0–G3 event-side tables mostly empty for shock path

---

## Live verification (this audit)

| Check | Result |
|-------|--------|
| `lsof` PIDs 7323/7325 | Both hold `data/market_events.db` + WAL |
| Poll 45s | `near_miss` **545822 → 545882** (+60); `market_events` 12→12; `paper_strategy_runs` 0→0 |
| `system_heartbeat` | Age ~0–1s continuously |
| Core log heartbeat | `events_detected=12`, `paper_runs_open=0`, `pending_reversals=0` |
| Detector rejections | `SHOCK_A–D: below_return_threshold`; `SHOCK_E: relative_move_failed` |
| Price feed | Many `fetch_ok`; intermittent Binance `fapi.binance.com` timeouts (AVAX/SUI…) |
| TradFi log | `fetch_ok=0`, `events_detected=0`, empty universe — **idle spin** |

---

## Why shock / paper rows are not appending

### Core (`--universe core`) — process healthy, detector quiet

1. **Runner is alive** (~76800+ cycles, heartbeat OK, near-miss writing).
2. **No new shocks** because every scan rejects: returns stay **below shock thresholds** (and SHOCK_E relative-move fails). Log: `below_return_threshold` / `relative_move_failed`.
3. Historical **12** events never progressed past `SHOCK_DETECTED` → **no pending rows, no paper opens** → `paper_strategy_runs` stays **0**.
4. Occasional Binance timeouts reduce coverage but do not stop the loop (`fetch_ok` still hundreds of thousands).

### TradFi (`--universe tradfi-liquid`) — process alive, nothing to poll

1. `market_events_instruments` count = **0**; universe log symbols = **`[]`**.
2. Heartbeat: `fetch_ok=0`, `events_detected=0`, `last_success_ts=none`.
3. Effectively a no-op poll loop sharing the same DB (may still touch heartbeat UPSERT).

### Not a DB-path bug

Both PIDs write the expected default DB. Absence of new shock/paper rows is **detector / confirmation / empty TradFi universe**, not “writing elsewhere.”

---

## Important recovery distinction

| System | Table | Written by shock-paper-run? |
|--------|-------|-------------------------------|
| Phase E shock paper | `paper_strategy_runs` | **Yes** (when reverse confirms) — currently **0** |
| S42 signal-learning paper | `market_events_paper_trades_s42` | **No** — different worker (`learning-worker` / S42) — also **0** locally |
| Research analytics | `market_events_trade_snapshots_s56` | **No** — research DB / postmortem |

The ~28k `futures_paper` universe is **not** produced by these shock-paper processes on this machine.

---

## Related docs

- `DATABASE_INVENTORY.md` — table row census
- `FILTER_PIPELINE.md` — gates (orthogonal to shock detect thresholds)
- `ARCHITECTURE.md` / `CURRENT_STATE.md` — universe policy
