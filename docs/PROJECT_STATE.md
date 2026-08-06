# PROJECT_STATE

Snapshot of the Polymarket BTC 5m / market-events research project for AI agents.

Last updated from branch `develop-terminal` after Math Decision Funnel V1 (`a041463`) and Research Infrastructure Finalization (`ff7973e`).

---

## Current project status

| Area | Status |
|------|--------|
| Research Lake V1 | Operational (`rlake-v1`, ~19205 lake rows on analytics DB) |
| Research Integrity V1 | PASS (`all_ok=True`, unexpected S55 = 0) |
| Reality Validation V1 | Operational (score ~85.98 on bound dataset hash) |
| Paper Math Books C/D | Operational (strict filters; Book D gated on Feature Store) |
| Forward Validation Monitor | Operational (observe-only; historical closed set tracked) |
| Elite Candidate / Profile / Audit | Operational (canonical table `elite_candidates_v1`) |
| Math Decision Funnel V1 | Operational (waterfall + rejectors + recoverable EV) |
| Documentation System V1 | This `docs/` pack |

Research freeze posture for validation stages: **observe Decision / Strategy / Gate / Execution**; analyze and report.

---

## Research stage

**Stage name:** Pre–production research validation (integrity-bound)

Completed research engines (recent, chronological):

1. Paper Decision Books A/B/C + journal
2. Decision Threshold Optimizer / Error Learning / Regime Transition
3. Elite Candidate → Market Profile → Profile Audit
4. Portfolio Simulator V1
5. Reality Validation Engine V1
6. Paper Mathematics Validation V1 (Books C/D math)
7. Forward Validation Monitor V1
8. Research Integrity Fix V1
9. Research Infrastructure Finalization V1 (write manager + infra self-test)
10. Math Decision Funnel V1

Active research question:

> Where does the mathematical decision funnel kill candidates, and is recoverable EV concentrated in modules that should stay strict?

Live funnel snapshot (Book A candidates ≈ 19205):

- Largest first-rejector: **Replay**
- Largest recoverable EV module: **Replay** (~16940 lost EV units on counterfactual winners)
- Book D accepted count on math books remains extremely selective (often 0 under full filters)

---

## Validation stage

| Gate | Meaning | Current |
|------|---------|---------|
| Integrity | Same lake hash / rows / version / build_ts; S55 unexpected = 0 | PASS |
| Reality | Adversarial score + dataset binding | Score ~85.98 bound |
| Paper Math | Strict C/D filters; Feature Store required for Book D | Running; selective |
| Forward | Observe-only trade tracking | Running; no Strategy mutation |
| Infra self-test | Integrity + Lake + Reality + Paper + Morning | Command: `research-selftest --infra` |

Nothing in this stage authorizes live Strategy or Gate changes.

---

## Frozen modules

Do **not** modify unless the user explicitly requests a targeted change:

- Gate
- Strategy
- Execution / Live adapters
- Paper execution adapter behavior
- Decision engine (`market_decision_v1` decide / veto logic)
- Optimizer / threshold optimizer promotion into production
- Reality Validation **scoring formulas** (consume + bind; do not retune to chase score)
- Elite **scoring** (consume via `load_canonical_elite`; do not local-filter)

Integrity/infra fixes that only touch write paths, joins, or report wiring are allowed when the task says so.

---

## Running modules

Research CLIs commonly used (all via `python -m bot.research.market_events <cmd>`):

| Command | Purpose |
|---------|---------|
| `research-integrity` | Canonical dataset + S55 + Book D FS gate |
| `research-selftest --infra` | Integrity / Lake / Reality / Paper / Morning |
| `research-lake-health` / `research-lake-build` / `research-lake-sync` | Lake ops |
| `reality-validation` / `reality-report` | Adversarial validation |
| `paper-math` / `paper-math-report` | Math Books C/D |
| `forward-monitor` / `forward-report` / `forward-weekly` | Forward observe |
| `decision-funnel` / `decision-waterfall` / `decision-rejectors` | Funnel analysis |
| `paper-decision-books` / `paper-book-report` | Decision journal A/B/C |
| `elite-candidates` / `elite-profile` / `elite-profile-audit` | Elite corpus |
| `portfolio-sim` | Research portfolio grids |
| `morning-report` | Daily research/ops brief |

Write path: `research_write_connection` + `research_write_manager` (no manual `BEGIN IMMEDIATE` in new code).

---

## Current goals

1. Keep Integrity green on one analytics DB across Cursor / Mini / CI
2. Use Decision Funnel to explain rejection pressure (especially Replay)
3. Keep Forward Validation observe-only — alert, do not auto-tune Strategy
4. Keep Book D refusing trades when Feature Store is missing
5. Maintain agent documentation in `docs/` as the permanent Hermes context

---

## Future roadmap

Ordered research track (not a commitment to change production):

1. Deep-dive Replay rejector: coverage vs threshold vs missing scores
2. Recoverable EV decomposition by coin / regime / weekday (still observe-only)
3. Longer Forward Validation windows with alert hygiene
4. Reality stress / leave-one-out narratives tied to funnel bottlenecks
5. Only after evidence hierarchy is satisfied: optional proposal for Decision changes (separate user approval)

Production promotion remains behind Reality + Forward + Integrity — see `docs/RESEARCH_RULES.md`.

---

## Known issues

1. **Replay funnel cliff** — nearly all Book A candidates fail Replay first; Fingerprint receives almost no survivors in sequential waterfall. Interpret carefully: first-rejector ≠ sole cause.
2. **Book D sparsity** — strict math + Feature Store gate → accepted n often 0; this is expected under freeze, not a license to relax filters.
3. **SQLite lock contention** — concurrent research writers can still log `database is locked`; use write manager + `retry_on_db_locked`; avoid nested long write leases during compute.
4. **Feature Store sample count** — FS may report a small `n_samples` relative to lake size; Book D requires FS present, not lake-complete coverage.
5. **Terminal vs research docs** — older terminal UI notes may exist elsewhere; this `docs/` pack is the Hermes research source of truth.
6. **Working tree noise** — many regenerated `*_REPORT.md` / alpha JSON artifacts may be dirty locally; do not commit unrelated report churn with research stages unless asked.

---

## Quick health commands

```bash
python -m bot.research.market_events research-selftest --infra
python -m bot.research.market_events research-integrity
python -m bot.research.market_events decision-funnel
```

End of project state.
