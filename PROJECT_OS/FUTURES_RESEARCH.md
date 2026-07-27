# PROJECT_OS — Futures Research

**Universe:** `futures_paper`  
**Status:** ACTIVE — primary research track  
**Updated:** 2026-07-26

---

## Mission

Improve futures **paper** trading quality using closed-trade analytics, open-time features, and leakage-safe discovery — without changing execution until explicitly approved.

Scale reference: **~28 000 paper trades**.

---

## What “good” looks like

1. Every report header: `universe=futures_paper`.  
2. Features available at **open** only for entry research.  
3. Labels (pnl, win, exit) joined by stable IDs — never mixed into feature columns unlabeled.  
4. LIVE S42 = operational truth for positions; RESEARCH S56 = analytical warehouse for closed rows.  
5. Filter / pattern / killer reports reproducible from a tagged dataset export.

---

## Active tool surface (market_events)

| Layer | Modules / CLIs (indicative) |
|-------|-----------------------------|
| Ingest / paper | `learning-worker`, `paper-performance`, S40/S42 |
| Features / explain | S55, S57, S58 |
| Analytics | S56, S59 Feature Lab, S61–S65, S64 PnL killers, S66 audit |
| Export | `research_dataset_builder`, `dataset_validator`, `dataset_provenance_audit` |

Full command inventory: `docs/ARCHITECTURE_AUDIT.md`.

---

## Near-term research agenda

1. **Inventory** production futures paper DB (~28k) — see `NEXT_TASK.md`.  
2. **Rebuild** open-time `lab_dataset` with `universe=futures_paper`.  
3. **Validate** (Phase 5C-style) on futures data.  
4. **Re-run** feature quality + PnL killers / long-short / filter sim **only** on futures_paper.  
5. **Lib consolidation** (metrics/ranking) behind fixtures.  
6. Optional: `load_lab_trades(universe="futures_paper")` as default after sign-off.

---

## Anti-goals

- Using `polymarket_hist` (1786) to tune futures gates.  
- Optimizing on leakage features (MFE, post-entry BTC moves, exit reason).  
- Collapsing LIVE/RESEARCH into one SQLite “for convenience”.  
- Silent inclusion of `hist:*` rows in futures paper dashboards.

---

## Related docs

- `PROJECT_OS/ARCHITECTURE.md`  
- `docs/TRADE_DATA_FLOW.md`  
- `docs/CONSOLIDATION_PLAN.md`  
- `docs/DATASET_SCHEMA.md` (schema pattern; rebuild for futures)
