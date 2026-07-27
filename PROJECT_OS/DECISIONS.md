# PROJECT_OS — Decisions Log

**Updated:** 2026-07-26  
Append-only. Newest first.

---

## 2026-07-26 — PROJECT_OS established; futures paper is primary

**Decision:** `PROJECT_OS/` is the single source of truth for product direction.  
**Decision:** Primary universe = **`futures_paper`** (~28k paper trades).  
**Decision:** **`polymarket_hist`** (local 1786 `hist:*` S56 rows) = **archived research branch**.  
**Decision:** Every future research module / report / dataset must **declare universe**.  
**Decision:** Documentation-only for this change — no trading code modified.

**Consequences:**

- Do not present polymarket hist metrics as futures paper edge.  
- Rebuild lab datasets on `futures_paper` before further ML on “the” dataset.  
- Phase 5A–5C outputs remain valid **for polymarket_hist archive only**.

---

## 2026-07-25 — Phase 5B open-time dataset (archive context)

**Decision:** Canonical feature rows exclude leakage (exit, pnl, mfe/mae, future BTC moves).  
**Decision:** Labels live outside the feature matrix (`metadata.json` labels).  
**Context:** Built from local RESEARCH S56 = **polymarket_hist**.  
**Follow-up:** Repeat builder with `universe=futures_paper`.

---

## 2026-07-25 — Phase 4A feature_utils extract

**Decision:** Shared pure helpers in `signal_intelligence/lib/feature_utils.py` without behavior change.  
**Decision:** Do not unify non-identical session/coin edge cases (S56 session invalid-hour, no-strip coin).

---

## 2026-07-25 — Consolidation principles (Phase 3)

**Decision:** Prefer extract-and-reuse; preserve LIVE/RESEARCH split; do not alter S40→S42→S55→S56→S59 spine.  
**Decision:** Delete only with proof.  
**Decision:** `load_lab_trades(universe=...)` is a later **product** decision (behavior change).

---

## Standing policies

| ID | Policy |
|----|--------|
| U1 | Forbidden to ship `mixed_unlabeled` analytics |
| U2 | LIVE paper journal (S42) ≠ RESEARCH warehouse (S56) |
| U3 | Hist backfill never silently rebranded as paper |
| U4 | No trading/open/close changes unless task explicitly requires |
| U5 | Agent handoffs start at `PROJECT_OS/HANDOFF.md` + `NEXT_TASK.md` |
