# PROJECT_OS — Next Task

**Updated:** 2026-07-28  
**Status:** ACTIVE — Research Pack 01 on production S55 history  
**Branch:** `develop-terminal`

---

## Context

Research Pack 01 (`trade-statistics`) summarizes **closed S55 paper trades**: aggregate stats, feature buckets, pair combos, winner/loser slice, market playbook. Outputs under `reports/research/`. No gate or strategy changes.

---

## Single next task

**Run `trade-statistics` on live DB and review playbook + bucket CSVs.**

### Goal

1. `python -m bot.research.market_events trade-statistics` after meaningful close volume.  
2. Read `reports/research/market_playbook.md` for profitable vs losing condition clusters.  
3. Cross-check sparse buckets with `dataset-audit` completeness.  
4. Document findings before any filter discussion (separate task).

### Acceptance

- [ ] `reports/research/statistics.md` generated on real data  
- [ ] Bucket / pair CSVs non-empty where features exist  
- [ ] Top/bottom playbook conditions reviewed

### Out of scope

- S55 threshold / EV / gate edits  
- ML or LLM layers  
