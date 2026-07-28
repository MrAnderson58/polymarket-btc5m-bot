# PROJECT_OS — Next Task

**Updated:** 2026-07-28  
**Status:** ACTIVE — grow Knowledge Engine on Mini-synced corpus  
**Branch:** `develop-terminal`

---

## Context

Knowledge Engine V1 stores validated analytics in `knowledge_features` / `knowledge_rules` / `knowledge_interactions` / `knowledge_history`.  
`feature-validation` auto-updates Knowledge DB and writes `reports/research/knowledge.md`.  
LLM (future) should read Knowledge only — not raw SQLite.

---

## Single next task

**Sync Mini (~2162 closes), run `feature-validation`, review `knowledge-show` KEEP/WATCH/REMOVE.**

### Acceptance

- [ ] Knowledge tables populated after validation  
- [ ] `reports/research/knowledge.md` reflects Mini-scale evidence  
- [ ] No gate / strategy edits

### Out of scope

- LLM chat over knowledge  
- Auto-applying rules to S55  
