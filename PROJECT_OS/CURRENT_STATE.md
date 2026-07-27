# PROJECT_OS — Current State

**Updated:** 2026-07-26  
**Branch tip (at write):** `develop-terminal`  
**Role of this folder:** single source of truth for *what we are building next*. Prefer `PROJECT_OS/` over scattered `docs/` notes when they conflict.

---

## Primary product

**Futures Paper Trading** (market-events learning / paper spine).

| Field | Value |
|-------|--------|
| Universe ID | `futures_paper` |
| Status | **ACTIVE — primary** |
| Scale (target / production reference) | **~28 000 paper trades** |
| Spine | `S40 → S42 → S55 → (S57/S58) → S56 → S59–S66` |
| Live DB | `data/market_events.db` (signals, paper journal, S55) |
| Research DB | `data/market_events_research.db` (S56–S66 analytics) |
| Ops | `learning-worker`, supervisor `start-all`, CLI `paper-performance` |

This universe is the **default** for strategy discovery, PnL analytics, filters, and ML datasets going forward.

---

## Archived research branch

**Polymarket hist / ER–shadow backfill** (local research snapshot).

| Field | Value |
|-------|--------|
| Universe ID | `polymarket_hist` |
| Status | **ARCHIVED — research only** |
| Scale (this workspace) | **1786** closed S56 rows (`hist:*`) |
| Provenance | `trades.db` → `backfill-history` → RESEARCH S56 |
| LIVE S42 on this machine | **0** (not a live paper book) |
| Lab export | `research/datasets/lab_dataset.*` (open-time features from hist) |
| Evidence | `docs/DATASET_PROVENANCE_AUDIT.md`, Phase 5A–5C reports |

Do **not** treat `polymarket_hist` metrics as futures paper performance. Do **not** mix the two universes in one report without an explicit `universe=` label.

---

## Number reconciliation (important)

| Figure | Meaning |
|-------|---------|
| **~28k** | Futures **paper** book (primary product scale / production reference) |
| **1786** | Local **Polymarket hist** RESEARCH S56 only |
| **28322 vs 22608** (in older Phase 2 notes) | Example of RESEARCH S56 ≫ LIVE S42 mismatch pattern — **not** “Polymarket is primary” |

If a machine shows ~28k in `audit-trade-data` / S56, confirm whether rows are `hist:*` or real paper (`g3_signal` / telegram / validation + S42). Always declare universe.

---

## Recent work completed (context)

| Phase | Deliverable | Notes |
|-------|-------------|--------|
| 1–3 | Architecture / trade-flow / consolidation docs | Planning |
| 4A | `lib/feature_utils.py` | Behavior-preserving helpers |
| 5A–5C | Feature audit, dataset builder, validator | Ran on **polymarket_hist** (1786) |
| Provenance | `dataset_provenance_audit` | Confirmed 1786 = hist-only locally |

**Not done:** migrate S59–S66 defaults to `futures_paper`; rebuild open-time dataset on futures paper; wire reports to declare universe.

---

## Hard constraints (unchanged)

- Do not change trading / open / close / gate semantics unless a task explicitly says so.
- Preserve LIVE vs RESEARCH DB separation.
- Prefer extract-and-reuse over rewrite.
- Delete code only with proof (orphans / dead registration).

---

## Where to look next

1. `PROJECT_OS/NEXT_TASK.md` — single next action  
2. `PROJECT_OS/ROADMAP.md` — ordered backlog  
3. `PROJECT_OS/ARCHITECTURE.md` — systems map + universe rules  
4. `PROJECT_OS/HANDOFF.md` — agent handoff checklist  

Legacy deep dives remain under `docs/` (ARCHITECTURE_AUDIT, TRADE_DATA_FLOW, CONSOLIDATION_PLAN, DATASET_*).
