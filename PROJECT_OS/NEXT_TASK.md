# PROJECT_OS — Next Task

**Updated:** 2026-07-27  
**Status:** ACTIVE — trade data collection  
**Branch:** `develop-terminal` (stable tip; SQLite final-elim WIP abandoned)

---

## Context

SQLite contention investigation closed — see `PROJECT_OS/SQLITE_FINAL_REPORT.md`.  
Acceptance (near-zero real locks) **not met**; unfinished write-path opts **not pushed**.  
Architectural writer-process deferred until **proven data loss or missed trades**.

---

## Single next task

**Collect and inventory live trading / paper data on the stable runtime.**

### Goal

1. Keep required market_events services running on stable `develop-terminal` (plus local feed proxy scrub if needed).  
2. Grow / verify **futures_paper** paper trade corpus (S42 opens/closes, features, learning).  
3. Inventory source of truth (~28k paper trades): paths, counts, signal-type mix — update `CURRENT_STATE.md` / `DECISIONS.md` as findings land.  
4. **Do not** resume SQLite micro-optimizations unless integrity gate trips.

### Acceptance (this cycle)

- [ ] Services healthy enough to collect (doctor may still WARN on SQLite locks — OK for now)
- [ ] Paper / learning paths writing; spot-check recent `paper_trades_s42` / features
- [ ] Documented DB paths + counts for futures_paper baseline
- [ ] No unfinished SQLite WIP pushed

### Out of scope

- Dedicated SQLite writer process  
- Further commit-batching / heartbeat micro-opts  
- Polymarket hist as futures baseline  

### Commands

```bash
python -m bot.research.market_events status
python -m bot.research.market_events doctor
python -m bot.research.market_events paper-performance
python -m bot.research.market_events start-all   # if stopped
```
