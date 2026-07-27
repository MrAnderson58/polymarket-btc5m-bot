# PROJECT_OS — Roadmap

**Updated:** 2026-07-26  
**North star:** Futures Paper Trading analytics & discovery on `universe=futures_paper` (~28k).  
**Polymarket hist (`polymarket_hist`, 1786)** stays archived — no new product features on it unless labeled research-only.

---

## Now → Next (ordered)

### P0 — Universe clarity (docs + flags, minimal code)

1. Tag every new research entrypoint with `universe=futures_paper|polymarket_hist`.  
2. Add provenance banner to high-traffic CLIs when next touched (`pnl-killers`, `morning-report`, `audit-trade-data`, dataset builder).  
3. Keep `PROJECT_OS/` updated on every phase ship.

### P1 — Futures paper dataset spine

1. Confirm production / primary DB has ~28k paper closes in S42/S56 (`futures_paper`).  
2. Rebuild canonical open-time dataset with `universe=futures_paper` → `research/datasets/`.  
3. Re-run validator + provenance; gate ML on green validation.  
4. Do **not** use the 1786 polymarket parquet as futures default.

### P2 — Behavior-preserving lib consolidation

From `docs/CONSOLIDATION_PLAN.md` (still valid):

1. `report_io.py`  
2. `metrics.py` + `trade_stats.py` (S59 then S64)  
3. `ranking.py`  
4. `snapshot.py` façade  

Fixture-guarded; no algorithm changes.

### P3 — Analytics on futures_paper

1. Re-run S59 / S61 / S62 / S64 / S65 / S66 with explicit universe.  
2. Feature quality audit on futures_paper (repeat Phase 5A methods).  
3. Leakage policy enforced in discovery / filter simulator.

### P4 — Optional product decisions (explicit sign-off)

1. `load_lab_trades(universe=...)` default → `futures_paper`.  
2. Hist filter off-by-default for paper reports.  
3. Provenance field on S56 (`live_close` | `s42_backfill` | `hist_backfill`).

### P5 — Housekeeping (proof-only deletes)

1. Duplicate `validation-report` CLI choice.  
2. Orphan `analyze_symbol_g36.py` if still unused.  
3. Archive stale Phase-E docs under `docs/research/archive/`.

---

## Explicitly deferred / out of scope here

- Redesigning S40→S42 open/close/gate logic  
- Merging LIVE and RESEARCH SQLite files  
- Live exchange execution changes  
- Treating Polymarket hist as production paper edge  
- Bulk CLI deletion without usage proof  

---

## Parallel tracks (do not confuse with futures paper)

| Track | Doc |
|-------|-----|
| Futures paper research | `PROJECT_OS/FUTURES_RESEARCH.md` |
| Polymarket archive | `PROJECT_OS/POLYMARKET_ARCHIVE.md` |
| Terminal V7 roadmap | `docs/ROADMAP.md` (UI) — secondary to futures paper spine |

---

## Done recently (do not redo)

- Phase 1–3 audits / consolidation plan  
- Phase 4A `feature_utils`  
- Phase 5A–5C + provenance on **polymarket_hist** (archive value only)
