# Project history

Cursor appends an entry after each completed task.

---

## 2026-07-27 — Runtime health tooling

**What:** `doctor` (SYSTEM STATUS), `watch`, `self-test`; SQLite lock-safe shadow batching docs from prior work referenced in operating flow.

**Why:** Single command to see live pipeline health without opening SQLite; htop-style watch; smoke test before/after deploy.

**Files:**
- `bot/research/market_events/runtime_health.py`
- `bot/research/market_events/doctor.py` (platform doctor alias)
- `bot/research/market_events/__main__.py`
- `tests/test_runtime_health.py`, `tests/test_market_events_doctor.py`
- `PROJECT_OS/PROJECT_HISTORY.md`, `NEXT_TASKS.md`, `OPERATING_RULES.md`

**Commit:** `d2f2f81` — Add runtime health tooling and project operating docs

**Verify next:**
- `python -m bot.research.market_events doctor`
- `python -m bot.research.market_events self-test --skip-network`
- `python -m bot.research.market_events watch --once`
- Restart shock-paper on Mac mini after `git pull`

---

## 2026-07-26 — Adaptive shadow profile + SQLite lock fix

**What:** `adaptive_v1` shadow A/B, `market_events_shadow`, batched writes, `shadow-report`, dashboard `/shadow-profiles`.

**Why:** Baseline shock thresholds too high vs market vol; measure adaptive band without changing production pipeline.

**Files:** `adaptive_shock_shadow.py`, `config.py`, `event_schema.py`, `paper_runner.py`, `PROJECT_OS/ADAPTIVE_SHOCK_PROFILE.md`, `SQLITE_LOCK_ANALYSIS.md`

**Commits:** `46e69f0`, `c232db6`

**Verify next:** `shadow-report --days 1`; ensure only one shock-paper-core PID holds DB.
