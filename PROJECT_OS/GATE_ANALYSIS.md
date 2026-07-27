# PROJECT_OS — Gate Analysis (`paper_strategy_runs` = 0)

_Read-only recovery. Sources: live `shock-paper-run` (PID 7323 core), `logs/me-shock-paper-core.log`, `data/market_events.db`, detector config. No code or data modified._

## Verdict

`paper_strategy_runs` stays **0** for two stacked reasons:

1. **Almost all polls never fire a shock** — quiet market vs fixed thresholds (`below_return_threshold` / `relative_move_failed`).
2. **Every detection that did persist (12/12) died before reversal monitoring** — cycle aborted in `link_event_context` → `_link_polymarket_state` querying missing column `market_checks.window_start_ts` on `trades.db`. Pending create / R1–R5 confirm / paper INSERT never ran.

Evidence: **12** rows in `market_events` all still `phase=SHOCK_DETECTED`; **0** `market_events_pending_shocks`; **0** lifecycle decisions; **12** log lines `cycle error: no such column: window_start_ts`.

---

## Funnel (core universe, lifetime of current process)

```
768,700  symbol price polls   (76,870 cycles × 10 symbols)
    ↓
715,526  fetch_ok             (93.1% — price available)
 53,174  fetch_failed         ( 6.9% — Binance timeouts etc.)
    ↓
~3.84M   detector evaluations (fetch_ok-scale × 5 detectors; see notes)
    ↓
≈100%    reject return/relative on a typical quiet cycle
    ↓
     12  detections persisted to market_events
    ↓
     12  aborted at context-link gate  (window_start_ts)
    ↓
      0  MONITORING_REVERSAL / pending shocks
    ↓
      0  reversal confirmations (R1–R5)
    ↓
      0  paper_strategy_runs
```

### Compact form (requested style)

```
768700 market updates (symbol-polls)
    ↓
715526 priced
    ↓
~3843500 detector checks
    ↓
12 detections (mostly SHOCK_E)
    ↓
0 pass context-link / pending create
    ↓
0 confirmation
    ↓
0 paper trade
```

TradFi runner (`--universe tradfi-liquid`): **empty universe** (`instruments=0`, `fetch_ok=0`) → funnel is all zeros; ignored below.

---

## Gate-by-gate report

Methodology notes:

- Lifetime counters from last heartbeat: `cycles=76870`, `fetch_ok=715526`, `fetch_failed=53174`, `events_detected=12`.
- Heartbeat `detector_rejections` = **last cycle only** (replaced each poll), not cumulative. Typical quiet cycle: **10/10** symbols fail each detector.
- Lifetime detector pass/fail exact counts are not persisted; estimates marked **≈**.

### Gate 1 — Price fetch

| Field | Value |
|-------|-------|
| **Gate name** | Price poll (`BinanceFuturesPriceFeed.poll_universe`) |
| **Evaluated** | 768,700 symbol-polls |
| **Passed** | 715,526 (`fetch_ok`) |
| **Rejected** | 53,174 (`fetch_failed`) |
| **Rejection %** | **6.92%** |
| **Rejection reason** | HTTP timeout / missing tick (`fapi.binance.com` read timeouts in log) |

---

### Gate 2 — SHOCK_A absolute return

| Field | Value |
|-------|-------|
| **Gate name** | `SHOCK_A` (`window=30s`, `min_abs_return_pct=1.5`) |
| **Evaluated (last cycle)** | 10 symbols |
| **Passed (last cycle)** | 0 (`fired` never appears in 9,107 heartbeats) |
| **Rejected (last cycle)** | 10 |
| **Rejection %** | **100%** (quiet cycle) |
| **Rejection reason** | `below_return_threshold` |
| **Lifetime detections using A** | **1** of 12 (`ADA` id=5 also fired A+B+E) |

---

### Gate 3 — SHOCK_B absolute return

| Field | Value |
|-------|-------|
| **Gate name** | `SHOCK_B` (`window=60s`, `min_abs_return_pct=2.0`) |
| **Evaluated (last cycle)** | 10 |
| **Passed** | 0 |
| **Rejected** | 10 |
| **Rejection %** | **100%** |
| **Rejection reason** | `below_return_threshold` |
| **Lifetime detections using B** | **1** / 12 |

---

### Gate 4 — SHOCK_C absolute return

| Field | Value |
|-------|-------|
| **Gate name** | `SHOCK_C` (`window=180s`, `min_abs_return_pct=3.0`) |
| **Evaluated (last cycle)** | 10 |
| **Passed** | 0 |
| **Rejected** | 10 |
| **Rejection %** | **100%** |
| **Rejection reason** | `below_return_threshold` |
| **Lifetime detections using C** | **0** / 12 |

---

### Gate 5 — SHOCK_D return + volume

| Field | Value |
|-------|-------|
| **Gate name** | `SHOCK_D` (`window=60s`, `min_abs_return_pct=1.5`, `min_volume_zscore=2.0`) |
| **Evaluated (last cycle)** | 10 |
| **Passed** | 0 |
| **Rejected** | 10 |
| **Rejection %** | **100%** |
| **Rejection reason** | `below_return_threshold` (volume gate not reached on quiet cycles; `volume_condition_failed` never logged in heartbeats) |
| **Lifetime detections using D** | **0** / 12 |

---

### Gate 6 — SHOCK_E relative move

| Field | Value |
|-------|-------|
| **Gate name** | `SHOCK_E` (`window=60s`, `min_relative_return_pct=1.0` vs BTC) |
| **Evaluated (last cycle)** | 10 |
| **Passed** | 0 |
| **Rejected** | 10 |
| **Rejection %** | **100%** |
| **Rejection reason** | `relative_move_failed` |
| **Lifetime detections using E** | **12** / 12 (dominant fire path) |

---

### Gate 7 — Persist detection (INSERT `market_events`)

| Field | Value |
|-------|-------|
| **Gate name** | `_persist_event` / dedup unique key |
| **Evaluated** | Shock candidates that fired (≥12 lifetime; duplicates not in heartbeat) |
| **Passed** | **12** rows in DB |
| **Rejected** | Unknown exact (`duplicate_event` / INSERT fail); heartbeat does not accumulate |
| **Rejection %** | n/a (not instrumented lifetime) |
| **Rejection reason** | Would be `duplicate_event` (`dedup_key` within `SHOCK_DEDUP_WINDOW_SEC=300`) |

All 12: `phase` still `SHOCK_DETECTED`; 11× ADA/DOGE **SHOCK_E-only**; 1× ADA A+B+E.

---

### Gate 8 — Context link (pipeline continuity) — **kills paper path**

| Field | Value |
|-------|-------|
| **Gate name** | `event_context_linker.link_event_context` → `_link_polymarket_state` |
| **Evaluated** | **12** (once per persisted detection; uncaught SQL error) |
| **Passed** | **0** |
| **Rejected** | **12** |
| **Rejection %** | **100%** |
| **Rejection reason** | `no such column: window_start_ts` — SQL selects `market_checks.window_start_ts` but `trades.db.market_checks` columns are `id, market_slug, seconds_remaining, strike_price, btc_price, yes_bid, yes_ask, no_bid, no_ask, signal, checked_at` |

Effect in `paper_runner.run_once` order:

1. INSERT `market_events` ✅  
2. INSERT `market_event_snapshots` ✅  
3. `link_event_context` ❌ raises  
4. **`create_pending_shock` never reached**  
5. R1–R5 / `_open_paper_runs` never reached  

Outer loop logs `cycle error` and does **not** roll back already-buffered writes; later `commit()` leaves events stuck at `SHOCK_DETECTED` with **no pending row**.

Log: **12** × `cycle error: no such column: window_start_ts` (= detection count).

---

### Gate 9 — Pending reversal create / monitor

| Field | Value |
|-------|-------|
| **Gate name** | `pending_reversal.create_pending_shock` (`MONITORING_REVERSAL`, max 900s) |
| **Evaluated** | **0** (never called after Gate 8) |
| **Passed** | 0 |
| **Rejected** | 0 (not evaluated) |
| **Rejection %** | n/a |
| **Rejection reason** | Upstream abort — table empty |

---

### Gate 10 — Reversal confirmation R1–R5

| Field | Value |
|-------|-------|
| **Gate name** | `reversal_confirmation.evaluate_all_reversals` / `process_pending_shock` |
| **Evaluated** | **0** candidates |
| **Passed** | 0 |
| **Rejected** | 0 |
| **Rejection %** | n/a |
| **Would-be rejection reasons** (if reached) | R1 reclaim &lt; 35% of shock; R2 insufficient ticks; R3 velocity; R4 volume climax; R5 survival &lt; 30s; or `EXPIRED_NO_REVERSAL` after 900s |

---

### Gate 11 — Paper strategy INSERT

| Field | Value |
|-------|-------|
| **Gate name** | `_open_paper_runs` → `INSERT OR IGNORE INTO paper_strategy_runs` |
| **Evaluated** | **0** |
| **Passed** | **0** |
| **Rejected** | 0 |
| **Rejection %** | n/a |
| **Rejection reason** | Never reached — would open up to 5 exit variants (`EXIT_A`…`EXIT_E`) per confirmed reversal |

---

## Top 10 rejection reasons

| Rank | Reason | Where | Scale / evidence |
|------|--------|-------|------------------|
| 1 | `below_return_threshold` | SHOCK_A–D | Dominant quiet-cycle reject; heartbeat always `=10` per A–D; summed across 9,107 HB snapshots ≈ **364k** displayed counts |
| 2 | `relative_move_failed` | SHOCK_E | Same; ≈ **91k** displayed across HB snapshots |
| 3 | `no such column: window_start_ts` | Context-link after detect | **12/12** detections aborted; **blocks all paper** |
| 4 | Price `fetch_failed` | Poll | **53,174** / 768,700 (**6.92%**) |
| 5 | Empty TradFi universe | tradfi-liquid runner | `fetch_ok=0`, `events_detected=0` forever |
| 6 | Absolute return too weak for A/B/C | Lifetime detects | Only **1/12** cleared 1.5%/2.0%; **0/12** cleared 3.0% |
| 7 | `duplicate_event` (possible) | Persist | Not observed in HB; possible under 300s dedup |
| 8 | `volume_condition_failed` | SHOCK_D | **0** HB mentions — return gate fails first |
| 9 | `insufficient_history` | Detectors | **0** HB mentions on warmed process |
| 10 | Reversal expire / R1–R5 fail | Confirm | **Never evaluated** (0 pending) — latent next blocker |

---

## Why detections look “alive” but paper is dead

| Artifact | Count | Meaning |
|----------|------:|---------|
| `market_events` | 12 | Detect INSERT succeeded |
| `market_event_snapshots` | 12 | Snapshot before context-link |
| `market_events_signal_trace_f51` RAW PASS | 12 | Hook before context-link |
| `phase=SHOCK_DETECTED` | 12/12 | Never advanced to `MONITORING_REVERSAL` |
| `market_events_pending_shocks` | 0 | Create skipped |
| `market_event_lifecycle_decisions` | 0 | Confirm logging skipped |
| `paper_strategy_runs` | **0** | Open skipped |
| Near-miss summaries | ~546k+ | Unrelated heartbeat path still writes |

---

## Threshold reference (fixed)

| Detector | Window | Threshold |
|----------|-------:|-----------|
| SHOCK_A | 30s | \|return\| ≥ **1.5%** |
| SHOCK_B | 60s | \|return\| ≥ **2.0%** |
| SHOCK_C | 180s | \|return\| ≥ **3.0%** |
| SHOCK_D | 60s | \|return\| ≥ **1.5%** and volume z ≥ **2.0** |
| SHOCK_E | 60s | \|return − BTC\| ≥ **1.0%** |

Reversal (if pending existed): R1 reclaim 35% of shock; R5 survival 30s; monitor max **900s**.

---

## Related PROJECT_OS docs

- `LIVE_WRITE_PATH.md` — what shock-paper writes when healthy  
- `FILTER_PIPELINE.md` — S55/S57/NO_C filters (different paper system than `paper_strategy_runs`)  
- `DATABASE_INVENTORY.md` — table census  

_Documentation only — no fix applied._
