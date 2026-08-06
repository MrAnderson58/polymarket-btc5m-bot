# RESEARCH_RULES

Rules for all research work in this repository. Binding for AI agents and humans.

Companion files: `docs/HERMES_SYSTEM_PROMPT.md`, `docs/PROJECT_STATE.md`, `docs/ARCHITECTURE.md`.

---

## Research philosophy

1. **Truth over PnL cosmetics** — a lower score that is honest beats a higher score that was tuned on the validation set.
2. **One canonical dataset** — all reports that claim comparability must share lake `dataset_version`, `lake_rows`, `build_ts`, and content `hash` (Research Integrity).
3. **Observe before change** — Decision / Gate / Strategy / Execution stay frozen during validation stages unless the user explicitly opens them.
4. **Try to disprove** — Reality Validation exists to break the story, not to confirm it.
5. **Attribution before intervention** — Decision Funnel / rejectors / recoverable EV explain *where* the stack fails before anyone edits modules.

---

## Forward validation rules

Forward Validation Monitor V1 is **observe-only**.

Allowed:

- Track new / closed trades across Books A–D
- Compute WR / PF / EV / Sharpe / prediction accuracy
- Emit alerts (reality drop, Book D underperformance, …)
- Write `FORWARD_VALIDATION_REPORT.md` / weekly reports

Forbidden:

- Auto-changing Strategy, Gate, Decision thresholds, or Optimizer from an alert
- Deleting historical tracked rows to “reset” metrics without an explicit user request
- Treating first-load historical ingest as “new alpha” without noting it is backfill

Interpretation:

- Alerts are hypotheses
- Promotion requires Reality + Integrity + explicit user approval

CLI: `forward-monitor`, `forward-report`, `forward-weekly`

---

## Replay rules

Replay is a **module score** on the decision journal (similarity / confidence scale), not a license to rewrite history.

Rules:

1. Use journal / lake closed trades as the candidate universe (Book A = baseline candidates).
2. Do not backfill Replay scores by re-running Strategy mid-validation unless the task is explicitly a data repair.
3. Funnel analysis must report Replay as first-rejector when it is; do not silently skip Replay to make later stages look populated.
4. Recoverable EV attributed to Replay means: rejected by Replay first, yet Book A counterfactual PnL > 0. That is an **investigation lead**, not proof Replay should be disabled.
5. Missing Replay (`NULL` / below floor) counts as fail under Math Decision Funnel floors (`MATCH_FLOOR = 0.50`).

Related: Timeline and Fingerprint are separate modules with their own floors (Timeline ≥ 0.60, Fingerprint ≥ 0.30 in funnel observation mirrors).

---

## Reality validation

Reality Validation Engine V1 adversarially tests the research edge.

Required behaviors:

- Persist dataset binding: `dataset_version`, `lake_rows`, `build_ts`, `hash`, `reality_score`
- Same DB + same hash ⇒ same score on Cursor / Mini / CI (Integrity FIX 1)
- Fail loudly on hash / row / version / recompute mismatch
- Writes go through research write manager (no manual `BEGIN IMMEDIATE`)

Allowed uses of the score:

- Gate Book C/D entry checks (`MIN_REALITY = 80` in paper-math filters — observe existing floors)
- Consistency sections in paper-math reports
- Forward alerts on reality drop

Forbidden:

- Retuning Reality formulas solely to raise the number for a demo
- Comparing Reality scores across different lake hashes without declaring the mismatch

CLI: `reality-validation`, `reality-report`

---

## Production promotion rules

Nothing research-side is “production” until all of the following hold:

1. **Integrity PASS** — canonical dataset; unexpected S55 = 0 (or documented impossibility with samples)
2. **Reality** — score and binding present; no unresolved recompute mismatch
3. **Forward** — observe window reviewed; no silent Strategy patch from alerts
4. **Explicit user approval** — Gate / Strategy / Decision / Execution changes are a separate task
5. **CLI shipped** — if a new command is part of the promotion tooling: commit + push + `--help` smoke

Paper Math Books C/D and Elite stores are **research paper / research elite**, not live exchange orders.

---

## Curve fitting rules

Treat as curve-fitting risk:

- Optimizing thresholds on the same window used for Reality / Paper Math acceptance
- Dropping modules from the funnel to inflate later-stage counts
- Re-scoring Elite with a new formula and comparing to old Reality without rebind
- Selecting coins / regimes after seeing OOS labels
- Expanding Feature Store samples only for trades that would pass Book D

Mitigations:

- Walk-forward / OOS / leave-one-out / stress inside Reality
- Integrity dataset hash freeze during a validation campaign
- Report sample sizes and rejector attribution with every “edge” claim
- Prefer holdout and forward windows over in-sample PF

---

## Evidence hierarchy

From strongest to weakest for research claims:

1. **Integrity-bound Reality** on a declared lake hash
2. **Forward Validation** outcomes after the claim date (true forward)
3. **Paper Math Book D** accepts under frozen filters + Feature Store present
4. **Decision Funnel / module influence** on the full Book A corpus
5. **Elite Profile Audit** biases / leakage checks
6. **Portfolio Simulator** grids (research-only capital paths)
7. **Single report tables** without hash / sample / rejector context
8. **Anecdotes / screenshots / chat memory**

If a stronger layer contradicts a weaker one, the stronger layer wins.

---

## Write and shipping discipline

- Research SQLite writes: `research_write_manager` + process flock
- New engines: schema + `event_schema._ensure_*` + CLI choices + dispatch + tests
- Do not claim done on uncommitted or unpushed CLI registration
- Do not commit secrets
- Do not mix unrelated dirty report JSON into research stage commits unless asked

---

## Summary

Research may measure everything.

Research may propose changes.

Research may not silently become production.

End of research rules.
