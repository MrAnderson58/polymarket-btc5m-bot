# PROJECT_OS — Next Task

**Updated:** 2026-07-28  
**Status:** ACTIVE — execute Research Inventory roadmap step 1 (Mini sync)  
**Branch:** `develop-terminal`

---

## Context

Research Inventory V1 is documented in `PROJECT_OS/RESEARCH_INVENTORY_V1.md`.  
On this Mac analytics DB: **42** closed S55 trades; overall EV negative (Pack01).  
Decision surface remains S57 regime + S55 neighbor EV only; most research is human-only.

---

## Single next task

**Bring Mini (~2162 closes) onto Research Sync and re-run Pack01.**

### Goal

1. Mini: `research-sync-doctor` → confirm which DB has ~2162 `s55_closed`.  
2. Set `MARKET_EVENTS_RESEARCH_ANALYTICS_DB` if needed; `research-sync-export`.  
3. Mac: `research-sync-import --file … --activate` → `SYNCED`.  
4. Both machines: matching `trade-statistics` totals.  
5. Update inventory §6 with Mini-scale evidence (no gate changes).

### Acceptance

- [ ] Snapshot manifest `s55_closed` ≈ production Mini count  
- [ ] `research-sync-status` = SYNCED on Mac after import  
- [ ] Pack01 n≥30 buckets appear where features vary  

### Out of scope

- Changing S55 / S57 thresholds  
- Removing orphan feature columns  
- ML / LLM training  
