# PROJECT_OS — Next Task

**Updated:** 2026-07-28  
**Status:** ACTIVE — use Expectancy Intelligence on live S55 rejections  
**Branch:** `develop-terminal`

---

## Context

Expectancy Intelligence V1 shipped (diagnostics only — no gate/threshold changes):

- `expectancy-breakdown`, `feature-importance`, `similar-trades`, `counterfactual`, `daily-intelligence`
- Auto `ti_paper_knowledge` on every S42 paper close

Use these reports to explain **NEGATIVE_EXPECTANCY** volume and neighbor-pool quality before any filter tuning.

---

## Single next task

**Run daily intelligence + breakdown on production DB; document top EV failure modes.**

### Goal

1. `daily-intelligence` → `reports/daily/YYYY-MM-DD.md` each session.  
2. `expectancy-breakdown --hours 24` after meaningful S55 traffic.  
3. Spot-check `similar-trades BTC` vs worst rejected candidates.  
4. Only propose gate changes after written evidence from counterfactual + breakdown (separate task).

### Acceptance

- [ ] At least one saved daily report on real data
- [ ] Breakdown summary buckets populated (not all zeros)
- [ ] Knowledge rows growing on paper closes (`ti_paper_knowledge`)

### Out of scope

- Changing S55 thresholds or opening logic  
- ML training / LLM layers  
- SQLite writer process  
