# PROJECT_OS — Next Task

**Updated:** 2026-07-28  
**Status:** ACTIVE — run Feature Validation on Mini-synced DB  
**Branch:** `develop-terminal`

---

## Context

Feature Validation V1 (`feature-validation`) ranks Funding / Trend / OI / Vol / AI / F&G / Neighbor EV / Regime / Direction into KEEP / WATCH / REMOVE. Research only — no gate changes.

---

## Single next task

**Sync Mini corpus, then re-run Feature Validation as strategy design input.**

### Goal

1. Research Sync Mini DB (~2162 closes) onto Mac.  
2. `python -m bot.research.market_events feature-validation`  
3. Treat `reports/research/feature_validation.md` as the baseline for next strategy version (still no auto-apply).

### Acceptance

- [ ] Report has KEEP/WATCH/REMOVE with n≥30 where possible  
- [ ] Stability section reviewed  
- [ ] No gate edits without explicit follow-up task

### Out of scope

- Removing similarity dims in live S55  
- Changing S57 / S55 thresholds  
