# Consolidation Plan — Phase 3

**Date:** 2026-07-25  
**Inputs:** `docs/ARCHITECTURE_AUDIT.md` (Phase 1), `docs/TRADE_DATA_FLOW.md` (Phase 2)  
**Constraint:** Planning document only — **no application code changes** in this phase.

**Principles**

1. Prefer extract-and-reuse over rewrite.  
2. Preserve LIVE / RESEARCH separation.  
3. Do not alter the S40 → S42 → S55 → S56 → S59 spine.  
4. Delete only with proof (orphans, dead registration, stale docs).  
5. Behavior-preserving refactors first; semantic filters (`hist:*` vs paper-only) are a later, explicit decision.

---

## 1. What can be unified without changing behavior?

These are **mechanical consolidations**: same formulas / same IO, moved behind one import. Call sites keep current argument shapes and return keys where possible.

### 1.1 Shared PF / WR / expectancy / MaxDD metrics

| Today | Evidence |
|-------|----------|
| Near-copies | `feature_lab_s59.lab_bucket_metrics`, `market_regime_s57.bucket_metrics`, `trade_postmortem_s56.bucket_metrics`, `trading_intelligence_report_s621.overall_performance`, `pattern_discovery_s623.pattern_metrics_fast`, `pnl_killers_s64._segment_metrics` |

**Unify into:** one pure function over a list of `{pnl_usd, …}` rows:

- trades, winrate, expectancy, profit_factor / pf_inf, sharpe (optional), max_drawdown (time-ordered), avg_pnl / net_pnl

**Behavior-safe if:**

- Floating rounding stays at current precision (or document a single rounding policy and accept tiny report diffs).  
- Callers that omit MaxDD keep omitting it (optional kwargs).  
- `pf_inf` semantics preserved (gross_loss ≈ 0 and wins > 0).

**Not behavior-safe yet:** changing which rows are included (e.g. dropping `hist:*`) — that is a product decision, not a refactor.

### 1.2 Shared ranking helper

| Today | Evidence |
|-------|----------|
| Repeated sorts | `coin_ranking` / hour / direction in S62.1; top losses/wins in S64 / S64.1; postmortem deep ranks; morning report slices |

**Unify into:** `rank_segments(rows, key_fn, *, by="net_pnl"|"pf", top_n=…)` returning the same card shape used by S64 (`trades`, `win_rate`, `profit_factor`, `net_pnl`, `avg_pnl`, `max_dd`).

**Behavior-safe if:** sort keys and tie-breakers match the caller being migrated (migrate one CLI at a time; golden JSON fixtures).

### 1.3 Shared snapshot writer façade

| Today | Three paths |
|-------|-------------|
| Live close | `record_close_snapshot` via `write_close_analytics` — INSERT OR REPLACE |
| S42 backfill | `backfill_snapshots` — batch close snapshots |
| History | `history_backfill_s621._upsert_snapshot` — INSERT OR IGNORE + `hist:*` keys |

**Unify into:** `write_snapshot(conn, payload, *, mode="replace"|"ignore", provenance=…)` that all three call.

**Behavior-safe if:**

- Default modes stay identical per entry point.  
- UNIQUE key remains `(s40_signal_type, s40_signal_id)`.  
- No silent switch of IGNORE ↔ REPLACE.

**Nice additive (still safe if optional):** `provenance` in `snapshot_json` (`live_close` | `s42_backfill` | `hist_backfill`) — does not change metrics unless reports filter on it.

### 1.4 Shared metrics / trade_stats helper (umbrella)

Beyond PF/WR: duration averages, equity-curve MaxDD, contribution-to-loss % (S64.1), gross win/loss.

**Unify into:** `trade_stats.py` with small composable functions (not one god-object).

### 1.5 Shared report helper

| Today | Scattered |
|-------|-----------|
| Markdown tables | S62.1, S63, S64, S64.1, S65, S66 each format PF/∞ and `%` ad hoc |
| JSON + path export | Repeated `mkdir` / `write_text` / `export_paths` |

**Unify into:** `report_io.py` — `write_report_pair(dir, stem, markdown, payload)` and `fmt_pf` / `fmt_num`.

**Behavior-safe if:** file names and JSON keys for existing CLIs stay unchanged.

### 1.6 Shared feature / attribution utils

| Today | Scattered |
|-------|-----------|
| Session from hour | S55 `_session_from_hour`, S66 audit, S56 close packing |
| AI scale 0–1 → 0–100 | pattern discovery, drift, intelligence |
| Confidence extraction | S64 / S66 / snapshot JSON |
| Coin normalize | `.upper().replace("USDT","")` in many places |

**Unify into:** `feature_utils.py` (pure helpers). Wire S55/S56/S66 to import the same functions — output strings/numbers should match current helpers.

### 1.7 Explicit non-goals for “no behavior change”

- Do **not** merge LIVE and RESEARCH connections.  
- Do **not** make `audit-trade-data` read S42 by default (would change counts).  
- Do **not** delete CLI commands in bulk.  
- Do **not** change gate / open / close ordering in the learning worker.

---

## 2. What should stay as-is?

### 2.1 LIVE / RESEARCH database split — keep

| Why keep | Detail |
|----------|--------|
| Lock isolation | Paper tick/open must not contend with heavy analytics on one SQLite file |
| Schema policy | Live migrations stop at v65; research owns v66–72 |
| Failure domains | Analytics bugs must not corrupt the paper ledger |

**Do not “simplify” by writing S56 back onto LIVE.** If anything, make the split *more* visible in CLI banners (“universe=research_s56”).

### 2.2 S42 as operational journal — keep

- Source of truth for open/closed paper positions, equity, replace/stale closes.  
- Written only on the live trading path (`open_paper_trades_from_s40`, tick, `_close_trade`).  
- Not an analytics warehouse.

### 2.3 S56 as analytical base — keep

- Source of truth for S59–S66 reports via `load_lab_trades`.  
- May intentionally be a **superset** of S42 (history backfill).  
- That supersets is a **documentation / labeling** problem, not a reason to delete S56 or fold it into S42.

### 2.4 S55 open-time features + S58 decision trace — keep both

Overlapping fields are real, but roles differ:

| Store | Role |
|-------|------|
| S55 | Gate / similarity / live feature vector at open |
| S58 | Explainability (`why_opened`, EMAs, inputs_json) on research DB |

Consolidation = shared *builders*, not collapsing tables.

### 2.5 Learning-worker cycle order — keep

```
ingest S40 → reviews → paper cycle (portfolio → open → tick → reports)
```

Reordering risks missed opens or racey closes.

### 2.6 Supervisor vs manual CLIs — keep both layers

~12 supervisor workers vs ~180 manual research CLIs. Shrinking the CLI surface is optional ops hygiene, not required for metrics consolidation.

---

## 3. What should move into a library?

Suggested package location (illustrative — not created in this phase):

`bot/research/market_events/signal_intelligence/lib/`  
or  
`bot/research/market_events/analytics/`

| Module | Contents | First consumers |
|--------|----------|-----------------|
| **`metrics.py`** | PF, WR, expectancy, sharpe, gross win/loss, `pf_inf` | S59, S57, S56, S62.1, S62.3, S64 |
| **`trade_stats.py`** | MaxDD on equity curve, avg hold, contribution % | S62.1 `overall_performance`, S64, S64.1 |
| **`ranking.py`** | Group-by dimension + top-N best/worst | S62.1, S63, S64, S64.1, S65 |
| **`snapshot.py`** | Normalize payload, write replace/ignore, provenance tag | S56 close, S56 backfill, S62.1 history |
| **`feature_utils.py`** | session, coin, AI scale, confidence, funding buckets labels | S55, S58 packing, S66, pattern/drift |
| **`report_io.py`** | markdown/json export helpers, number formatting | S62.1–S66 report writers |
| **`lab_load.py`** (optional thin wrap) | Re-export `load_lab_trades` + future `universe=` flag | All lab consumers — **default universe unchanged** |

### Migration order (lowest risk first)

1. `feature_utils.py` + `report_io.py` (pure / IO only)  
2. `metrics.py` + `trade_stats.py` (swap `lab_bucket_metrics` call sites with tests)  
3. `ranking.py` (one report at a time)  
4. `snapshot.py` façade (wire history IGNORE and close REPLACE explicitly)

Each step: unit tests that compare old vs new helper on fixed row fixtures.

---

## 4. What can be removed? (only after proof)

### 4.1 Proven — safe to remove / fix

| Item | Proof | Action |
|------|-------|--------|
| Duplicate `validation-report` in `choices=` | Phase 1: listed twice; first handler (G4) always wins; S11 dispatch unreachable | Remove **duplicate choice entry**; keep **one** intentional command + one handler (decide G4 vs S11 explicitly before deleting the unused `if` branch) |
| `analyze_symbol_g36.py` | Phase 1: **0 importers**; `/analyze` routed to S50 | Delete module; grep once more for string refs in docs/telegram |

### 4.2 Proven stale docs — archive or update

| Item | Proof | Action |
|------|-------|--------|
| `.cursor/PROJECT_CONTEXT.md` drift vs market_events SI | Phase 1 | Update or mark archived — not runtime |
| Phase E plans that contradict current supervisor | Docs-only | Move to `docs/research/archive/` after confirming no ops links |

### 4.3 Not proven — do **not** delete yet

| Item | Why wait |
|------|----------|
| ~82 registration-only CLIs | Heuristic “never referenced outside `__main__`” ≠ unused by humans |
| G39 / G40 modules | Still imported by `runner_g3` |
| SHOCK_F shadow audit CLIs | Manual research; no proof of zero use |
| Older S31/S32 pattern agents | Still importable; confirm no telegram/CLI path |
| Duplicate metric *functions* | Remove only **after** library swap + green tests — delete copies, not features |

### 4.4 Removal checklist (mandatory before delete)

1. `rg` module name / CLI string across repo + docs + deploy.  
2. Confirm no supervisor / launchd / systemd reference.  
3. Run unit tests touching the area.  
4. Prefer one commit per deletion with revert-friendly message.

---

## 5. What must not be touched?

### 5.1 The spine (do not redesign)

```text
S40  signal ingest          (LIVE)
  ↓
S42  paper open / tick / close   (LIVE operational journal)
  ↓
S55  features at open (+ finalize on close)  (LIVE)
  ↓
S60  write_close_analytics bridge
  ↓
S56  closed snapshots (+ S58 decisions)      (RESEARCH)
  ↓
S59  feature lab / load_lab_trades consumers (RESEARCH analytics)
```

Also preserve adjacent spine pieces that closes depend on:

- **S57** regime attach/gate at open (feeds S55).  
- **S58** decision write on open/close (research explainability).  
- **S60** live→research boundary (`write_open_decision` / `write_close_analytics`).

### 5.2 Concrete “hands off” list

| Area | Reason |
|------|--------|
| `open_paper_trades_from_s40` / `_close_trade` / tick exit logic | PnL and risk correctness |
| S55 `should_open_trade` gate semantics | Changes which trades exist |
| Learning-worker cycle order | Open/close timing |
| LIVE migration ceiling v65 vs research v66–72 | Prevents analytics DDL on trading DB |
| UNIQUE `(s40_signal_type, s40_signal_id)` on S56 | Identity of analytical rows |
| `load_lab_trades` default SQL (until an explicit universe flag is added and documented) | All S59–S66 baselines |

### 5.3 Allowed near the spine (careful)

| Change | Condition |
|--------|-----------|
| S55/S58/S56 call shared `feature_utils` | Same outputs |
| Snapshot writer façade | Same REPLACE vs IGNORE per caller |
| CLI prints “lab_n vs paper_n” | Additive observability only |
| Optional `provenance` field | Additive JSON; filters off by default |

---

## Recommended Phase 4+ sequence (advisory)

| Step | Work | Behavior change? |
|------|------|------------------|
| 4a | Fix `validation-report` dup; delete `analyze_symbol_g36.py` | No (dead code) |
| 4b | Add `lib/feature_utils.py` + `report_io.py`; migrate S66/S64 formatters | No |
| 4c | Add `lib/metrics.py` + `trade_stats.py`; migrate S59 then S64 | No (fixture-guarded) |
| 4d | Add `lib/ranking.py`; migrate S64.1 / S62.1 | No |
| 4e | Add `lib/snapshot.py` façade | No |
| 4f | CLI labeling: paper vs lab universe counts | Observability only |
| 5+ | Optional: `load_lab_trades(universe="paper"|"hist"|"all")` | **Yes** — needs product sign-off |

---

## Summary answers

1. **Unify without behavior change:** metrics (PF/WR/E/MaxDD), ranking, snapshot write façade, feature/session/coin helpers, report IO formatting.  
2. **Leave as-is:** LIVE/RESEARCH split, S42 journal, S56 analytics warehouse, S55+S58 dual stores, worker order, supervisor topology.  
3. **Library extract:** `metrics.py`, `trade_stats.py`, `ranking.py`, `snapshot.py`, `feature_utils.py`, `report_io.py` (optional `lab_load.py` later).  
4. **Delete only with proof:** duplicate CLI registration, `analyze_symbol_g36.py`, clearly stale docs — not bulk CLIs or G39/G40.  
5. **Do not touch:** S40 → S42 → S55 → S56 → S59 spine and the S57/S58/S60 attachments that make it correct.

---

*End of Phase 3 Consolidation Plan. No application code was modified.*
