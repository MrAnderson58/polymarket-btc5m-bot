# ARCHITECTURE

Research architecture for the market-events / Polymarket BTC 5m bot.

This document describes the **research stack** under `bot/research/market_events/`. It does not authorize changes to Gate, Strategy, Execution, Paper adapters, Decision, or Optimizer.

---

## High-level data flow

```
S42 paper trades (closed)
        ↓
S55 trade features (collector / backfill stubs)
        ↓
Research Lake V1  ←── meta: dataset_version, build_ts, row hash
        ↓
Feature Store (offline parquet)     Decision Journal (Books A/B/C)
        ↓                                    ↓
   ML / math gates                    Module scores (Replay…Brain)
        ↓                                    ↓
 Reality Validation ←── Integrity binds lake hash
        ↓                                    ↓
 Elite store (elite_candidates_v1) ←── load_canonical_elite()
        ↓                                    ↓
 Paper Math Books C/D          Math Decision Funnel (observe)
        ↓                                    ↓
 Forward Validation Monitor (observe-only tracked set)
```

Analytics SQLite is resolved via research sync (`resolve_research_analytics_sqlite_path`). Writes use `research_write_connection` + `research_write_manager`.

---

## Research Lake

**Package:** `signal_intelligence/research_lake_v1/`  
**Tables:** `market_events_research_lake_v1`, `..._builds_v1`, `..._meta_v1`  
**Dataset version constant:** `rlake-v1`

Role:

- Canonical closed-trade research corpus
- Joins S55 via `paper_trade_id` (fallback S40 keys)
- Emits build metadata and content hash for Integrity

CLI: `research-lake-build`, `research-lake-sync`, `research-lake-health`

S55 join audit classifies missing joins (`NO_S55_RECORD`, `TIMESTAMP_MISMATCH`, expected stubs, …). Integrity requires **unexpected** gaps = 0 (or an explicit impossibility explanation).

---

## Decision Engine

**Package:** `signal_intelligence/market_decision_v1/`  
**Persist path:** Paper Decision Books journal `market_decision_journal_v1`

`decide_one` fuses module scores into a decision (`TRADE` / no-trade), confidence, rank, and reasons.

Research consumers **read** journal rows; they must not rewrite Decision veto logic during freeze.

Module columns on the journal:

| Module | Journal field |
|--------|----------------|
| Replay | `replay` |
| Fingerprint | `fingerprint_similarity` |
| Timeline | `timeline_similarity` |
| DNA | `dna` |
| Rules | `rules` |
| Edge | `edge` |
| Causality | `causality` |
| Brain | `brain` |

CLI related: `market-decision`, `market-decision-explain`, `paper-decision-books`

---

## Timeline

Timeline module scores **path similarity** of the trade’s temporal profile vs historical analogues.

- Stored as `timeline_similarity` (0–1)
- Paper-math / funnel observation floor: **≥ 0.60**
- Missing timeline is a stop in paper-math filters

It is independent of Fingerprint (shape/tag similarity) and Replay (replay similarity).

---

## Fingerprint

Fingerprint module scores **market/trade fingerprint** similarity.

- Stored as `fingerprint_similarity` (0–1)
- Funnel observation floor: **≥ 0.30**
- Elite / profile engines may enrich from store + journal

Fingerprint answers “does this look like known good shapes?” Timeline answers “does the path evolve similarly?” Replay answers “does historical replay support the trade?”

---

## Elite

**Package:** `signal_intelligence/elite_candidate_v1/` (+ profile / audit)  
**Table:** `elite_candidates_v1`  
**Canonical loader:** `research_integrity_v1.canonical.load_canonical_elite()`

Categories stored for research: `ELITE`, `A+`, `A` (IGNORE / B not in store categories).

Consumers that must use the canonical loader (no local re-score / LIMIT filtering):

- Elite Profile / Audit
- Morning Report elite slice
- Paper Math candidate enrichment
- Reality / Portfolio / Funnel elite sets as applicable

CLI: `elite-candidates`, `elite-profile`, `elite-profile-audit`, …

---

## Reality

**Package:** `signal_intelligence/reality_validation_v1/`  
**Table:** `reality_validation_v1`

Adversarial suite (walk-forward, OOS, Monte Carlo, stress, regimes, leave-one-out) → `reality_score` + overfitting diagnostics.

Integrity binds Reality to lake meta so Cursor / Mini / CI agree on the same DB.

CLI: `reality-validation`, `reality-report`

---

## Paper Books

Two layers:

### Decision Books (journal)

| Book | ID | Rule |
|------|-----|------|
| A | `paper_baseline` | All candidates (baseline / counterfactual PnL) |
| B | `paper_decision` | `decision == TRADE` |
| C | `paper_high_confidence` | Stricter confidence / module gates |

### Math Books (paper-math)

| Book | ID | Rule |
|------|-----|------|
| C | `paper_math_elite` | Full mathematical entry pass |
| D | `paper_strict_math` | Entry pass + no duplicate + **Feature Store required** |

Paper Math does not change Execution. Book D refuses with `feature_store_missing` when the offline Feature Store is absent.

CLI: `paper-decision-books`, `paper-math`, `paper-math-report`

---

## Forward Validation

**Package:** `signal_intelligence/forward_validation_v1/`  
**Tables:** `forward_validation_v1`, `forward_validation_state_v1`

Observe-only tracker for research books. Computes closed metrics and alerts. Does not mutate Strategy.

CLI: `forward-monitor`, `forward-report`, `forward-weekly`

---

## Decision Funnel

**Package:** `signal_intelligence/math_decision_funnel_v1/`  
**Tables:** `decision_funnel_v1`, `decision_rejections_v1`

Sequential waterfall (observation floors):

```
Candidate → Replay → Fingerprint → Timeline → DNA → Rules → Edge
        → Causality → Brain → Reality → Elite → Decision → Book B → Book D
```

Also produces:

- Top rejectors (first rejecting module counts)
- Recoverable EV (rejected but Book A PnL > 0)
- Module influence (ΔWR / ΔEV / ΔPF / ΔSharpe)

CLI: `decision-funnel`, `decision-waterfall`, `decision-rejectors`  
Reports: `DECISION_FUNNEL.md`, `DECISION_WATERFALL.md`, `TOP_REJECTORS.md`, `RECOVERABLE_EV.md`

---

## Research Integrity

**Package:** `signal_intelligence/research_integrity_v1/`

Ensures:

1. Reality dataset parity (hash / rows / version / build_ts)
2. One elite table for consumers
3. S55 / Feature Store audit + timestamp reconcile (&lt;60s auto, else report)
4. Book D Feature Store gate

CLI: `research-integrity`  
Infra bundle: `research-selftest --infra`

---

## Module relationships

```
                    ┌──────── Fingerprint
Historical analogues┤
                    ├──────── Timeline
                    └──────── Replay

Rules / DNA / Edge / Causality / Brain ──→ Decision (TRADE?)
                                              ↓
                                         Book B journal
                                              ↓
                              Elite store + Reality score
                                              ↓
                                         Paper Math C/D
                                              ↓
                                      Forward monitor
```

Integrity wraps the **dataset**, not the trading venue.

Funnel wraps the **same scores** already stored — it does not retrain modules.

---

## Package map (research)

```
bot/research/market_events/
  __main__.py                 # CLI
  event_schema.py             # migrations / _ensure_*
  research_db_session.py      # write lock + connections
  research_write_manager.py   # batched writes without BEGIN IMMEDIATE
  signal_intelligence/
    research_lake_v1/
    market_decision_v1/
    paper_decision_books_v1/
    elite_candidate_v1/
    reality_validation_v1/
    paper_math_validation_v1/
    forward_validation_v1/
    research_integrity_v1/
    math_decision_funnel_v1/
    research_infrastructure_v1/
    ...
```

---

## Agent documentation entrypoints

| File | Purpose |
|------|---------|
| `docs/HERMES_SYSTEM_PROMPT.md` | Role, process, allow/deny, workflows |
| `docs/PROJECT_STATE.md` | Live status, frozen vs running, issues |
| `docs/RESEARCH_RULES.md` | Validation / promotion / evidence hierarchy |
| `docs/ARCHITECTURE.md` | This file — modules and data flow |

End of architecture.
