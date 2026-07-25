# Architecture Audit — Phase 1

**Date:** 2026-07-25  
**Scope:** Entire repository `/Users/andrey/polymarket-bot/polymarket-btc5m-bot`  
**Method:** Static analysis only (source, docs, deploy, scripts). No runtime process inspection.  
**Constraint:** Application code was **not** modified for this audit. This document is the only deliverable.

**Confidence legend**

| Tag | Meaning |
|-----|---------|
| HIGH | Confirmed by source structure / imports / CLI registration |
| MEDIUM | Heuristic (string refs, naming); verify before action |
| LOW | Inferred / incomplete evidence |

---

## Executive summary

The repo contains **two product stacks**:

1. **Polymarket live bot** — `python -m bot.main` + `data/trades.db` (SQLite).
2. **Market-events research fleet** — `python -m bot.research.market_events` (~194 unique commands) + `data/market_events.db` / `data/market_events_research.db`, plus optional PostgreSQL.

Additionally: **futures_agent** (Telegram intake), **futures research**, **strategy_simulator**, **MTF**, and several older Polymarket research modules (`bidirectional_*`, V4 shadows).

**Key findings**

- `market_events` CLI has **195 `choices` entries / 194 unique** — `validation-report` is registered twice; first handler wins (G4), second (S11) is unreachable.
- **~12 supervisor workers** via `start-all`; production scripts (`prod-*.sh`) only manage **main bot + telegram**.
- Trade paper path spine: **S40 → S42 open → S55 features + S58 decision → close → S56 snapshot (research) → S59–S66 analytics**.
- Only one clear SI orphan module: `analyze_symbol_g36.py`.
- ~40% of `market_events` CLI commands are **registration-only** (manual research tools; not scheduler-wired).
- Heavy **duplicate analytics** (PnL/PF/ranking/regime/pattern) across S56–S66.

---

## 1. Every executable command

### 1.1 `python -m bot.research.market_events` (194 unique / 195 choices)

Source: `bot/research/market_events/__main__.py`.

#### Schema / DB (10)

`market-event-migrate` · `market-research-migrate` · `db-info` · `market-db-info` · `market-db-check` · `market-db-copy` · `migrate-to-postgres` · `market-db-benchmark` · `market-db-backup` · `market-db-restore`

#### Collectors / paper runners (3)

`shock-paper-run` · `observe-run` · `ai-worker-run`

#### Shock / observe reports & audits (20)

`observe-report` · `activation-explain` · `shock-event-report` · `shock-strategy-report` · `shock-context-report` · `shock-lifecycle-audit` · `shock-opportunity-audit` · `shock-f-shadow-audit` · `shock-f-v2-shadow-audit` · `tradfi-shock-readiness` · `shock-strategy-matrix-report` · `pending-reversal-report` · `shock-profile-report` · `reversal-counterfactual-report` · `shock-near-miss-report` · `collector-path-audit` · `market-alert-audit` · `polymarket-paper-audit` · `architecture-audit` · `cli-architecture-audit`

#### AI analysis (5)

`ai-analysis-audit` · `ai-event-report` · `ai-analyze-pending` · `ai-critic-replay-report` · `ai-test`

#### Instruments / E2 (4)

`instrument-discover` · `instrument-report` · `e2-audit` · `index-discovery-audit`

#### Historical replay (9)

`historical-replay-coverage` · `historical-candle-backfill` · `historical-candle-coverage` · `historical-shock-replay` · `historical-shock-report` · `historical-reversal-report` · `historical-strategy-matrix` · `historical-context-report` · `history-backfill`

#### Alert / digest / dashboard (9)

`market-heartbeat-preview` · `market-daily-digest` · `market-weekly-report` · `market-timeline-report` · `market-opportunity-report` · `market-ai-comparison-report` · `dashboard-api-serve` · `live-dashboard` · `demo-event`

#### Process supervisor / health (10)

`system-validation` · `start-all` · `stop-all` · `status` · `health` · `doctor` · `trading-audit` · `report` · `telegram-status` · `restart-telegram`

#### Telegram ops (10)

`telegram-alert-test` · `telegram-health` · `telegram-health-report` · `telegram-config` · `telegram-retry-unsent` · `telegram-debug` · `telegram-inbound-debug` · `telegram-self-test` · `simulate-telegram-message` · `emit-test-signal`

#### Signal intelligence F-series reports (19)

`multitimeframe-report` · `exhaustion-report` · `exchange-context-report` · `signal-quality-report` · `weekly-signal-ranking` · `trader-performance-report` · `trader-ranking-report` · `market-score-report` · `liquidation-report` · `whale-report` · `dominance-report` · `signal-trace-report` · `today-summary` · `yesterday-report` · `near-miss-report` · `detector-stats` · `threshold-report` · `liquidity-trend-report` · `threshold-simulator`

#### Claude / G2 / G3 (10)

`claude-test` · `claude-health` · `g2-trace` · `g3-run` · `g3-health` · `g3-trace` · `candidates` · `candidate-stats` · `candidate-coverage` · `candidate-replay`

#### Scoring / validation / quant (22)

`score-breakdown` · `score-correlation` · `score-recommendations` · `market-heatmap` · `heartbeat-trace` · `validation-report` (**DUP**) · `feature-importance` · `false-rejects` · `false-accepts` · `optimizer-report` · `quant-research` · `quant-report` · `quant-debug` · `research-data-audit` · `research-lake-build` · `market-memory` · `signal-discovery` · `why-not` · `trend-status` · `trend-history-report` · `experimental` · `threshold-optimizer`

#### Shadow / validation (7)

`shadow-report` · `shadow-open` · `shadow-trace` · `shadow-self-test` · `validation-open` · `validation-report` (**DUP**) · `validation-force`

#### Decision / pattern / news (11)

`decision` · `explain` · `explain-decision` · `pattern` · `pattern-build` · `news-update` · `news-latest` · `news-intel-worker` · `news-intel-aggregate` · `news-intel-brief` · `news-intel-reports`

#### Long-running intel workers (4)

`event-engine-run` · `multi-source-run` · `source-health` · `narrative-engine-run`

#### Learning / review / paper (7)

`reversal-diagnostics` · `signal-inbox` · `review` · `learning-status` · `learning-health` · `learning-worker` · `paper-performance`

#### Trade RCA / regime / discovery / PnL (16)

`trade-regression-audit` · `trade-postmortem` · `market-regime` · `decision-report` · `feature-lab` · `strategy-discovery` · `alpha-discovery` · `intelligence-report` · `explain-drift` · `discover-patterns` · `morning-report` · `pnl-killers` · `simulate-filters` · `long-short-analysis` · `audit-trade-data` · `backfill-history`

#### Research AI / suggestions (12)

`research-stress-test` · `trade-suggestions` · `approve-suggestion` · `reject-suggestion` · `research-ai` · `research-audit` · `research-cost` · `research-history` · `research-artifacts` · `research-compare` · `pipeline-audit` · `recorder-debug`

#### Debug / providers / locks (7)

`data-source-debug` · `volume-debug` · `env-debug` · `api-test` · `provider-status` · `sqlite-lock-debug` · `sqlite-lock-smoke`

### 1.2 Other `python -m bot.*` packages (`__main__.py`)

| Module | Purpose |
|--------|---------|
| `bot.ops` | `healthcheck`, `snapshot`, `prod-control {status\|start\|stop\|restart}` |
| `bot.research.futures_agent` | **44** Telegram/research commands |
| `bot.research.futures` | Futures signal pipeline (`parse`, `snapshot`, `outcomes`, `report`, …) |
| `bot.research.ai_analyst` | `run`, `paper`, `validate` |
| `bot.research.mtf` | Multi-timeframe research |
| `bot.research.strategy_simulator` | Simulate / discover / walk-forward / shadow-enable |
| `bot.research.market_behavior` | `report`, `edge-report`, `heatmap` |
| `bot.research.bidirectional_v13_execution` | V1.3 execution research |
| `bot.evolution` | Shadow evolution status |
| `bot.portfolio` | `reset-guards` |
| `bot.daily` | Daily compute |
| `bot.report` | Analytics report |
| `bot.optimizer` | Optimizer grid |
| `bot.live` | Live portfolio dashboard |
| `bot.live_check` | READY / NOT READY |
| `bot.ai_agent` | Daily AI agent sync (observe) |
| `bot.trading_brain` | Learning sync |
| `bot.scientist` | Hypothesis cycle |
| `bot.strategy_review` | Strategy review |

### 1.3 Runnable modules without package `__main__`

| Invoke | Purpose |
|--------|---------|
| `python -m bot.main` | **Production trading loop** |
| `python -m bot.er_stats` | Early Reversion counters |
| `python -m bot.analytics` | Settled-trades analytics |
| `python -m bot.fills_audit` | Fill audit |
| `python -m bot.clob_healthcheck` | CLOB health |
| `python -m bot.clob_order_debug_v2` | CLOB V2 debug |
| `python -m bot.collector_diagnostics` | Collector density |
| `python -m bot.bidirectional_check` | Bidirectional shadow health |
| `python -m bot.daily_report` | Alias → `bot.report` |
| `python -m bot.research.bidirectional_v12_research` | V1.2 research |
| `python -m bot.research.bidirectional_v12_counterfactual` | V1.2 counterfactual |
| `python -m bot.research.bidirectional_quote_alignment` | Quote alignment |
| `python -m bot.research.execution_failure_audit` | Execution failure audit |

### 1.4 Scripts

| Path | Purpose |
|------|---------|
| `scripts/prod-start.sh` / `prod-stop.sh` / `prod-restart.sh` / `prod-status.sh` | Prod main + telegram via `bot.ops` |
| `scripts/prod-common.sh` | Shared bootstrap |
| `scripts/backup-databases.sh` | SQLite backup + optional `pg_dump` |
| `scripts/diagnose_clob_403.py` | CLOB 403 diagnose |

**Absent:** `Makefile`, `package.json`, `pyproject.toml` / `console_scripts`, `bin/`.

---

## 2. Every scheduler

| Kind | Finding |
|------|---------|
| APScheduler / cron library / supervisord / Procfile | **None** in repo |
| Committed crontab | **None** (docs mention optional backup cron) |
| In-process tick | `bot/research/market_events/alert_engine/scheduler.py` — digest ticks from paper runner |
| launchd templates | `deploy/macos/com.polymarket.bot-main.plist`, `…futures-agent-telegram.plist` |
| systemd | `deploy/futures-agent-telegram.service` |
| Docker Compose | `deploy/docker/docker-compose.yml` — **Postgres only** (no app workers) |

### `start-all` managed services (12)

| Key | Command |
|-----|---------|
| `shock-paper-core` | `shock-paper-run --universe core --paper-only` |
| `shock-paper-tradfi` | `shock-paper-run --universe tradfi-liquid --paper-only` |
| `observe` | `observe-run --universe tradfi-observe` |
| `ai-worker` | `ai-worker-run` |
| `g3-live` | `g3-run` |
| `learning` | `learning-worker` |
| `news-intel` | `news-intel-worker` |
| `event-engine` | `event-engine-run` |
| `multi-source` | `multi-source-run` |
| `narrative-engine` | `narrative-engine-run` |
| `dashboard` | `dashboard-api-serve` |
| `telegram` | `bot.research.futures_agent telegram-poll` |

PID/meta: `data/market_events_supervisor/`.

### Long-running loops (HIGH)

`bot.main` · `telegram-poll` · `ai-worker-run` · `learning-worker` · `news-intel-worker` · `event-engine-run` · `multi-source-run` · `narrative-engine-run` · `g3-run` · `shock-paper-run` · `observe-run` · `live-dashboard` (refresh loop)

### Process control overlap (NEEDS REVIEW)

- `prod-*.sh` / `bot.ops prod-control` → **main + telegram only**
- `market_events start-all/stop-all` → research fleet **including telegram**
- Dual supervisors on telegram can conflict (`data/futures_agent_telegram_poll.lock`)

---

## 3. Every SQLite database

| Role | Default path | Env | Live vs research |
|------|--------------|-----|------------------|
| Polymarket primary | `data/trades.db` | `DATABASE_PATH` | **Live** (+ additive research tables) |
| Market events live | `data/market_events.db` | `MARKET_EVENTS_DATABASE_PATH` / `MARKET_EVENTS_DB_URL` | **Live ME** (signals, paper, S55) |
| Market events research (S60) | `data/market_events_research.db` | `MARKET_EVENTS_RESEARCH_DB_URL` | **Research-only** (S56–S62+) |
| Futures agent | `data/futures_agent.db` | `FUTURES_AGENT_SQLITE_PATH` / `FUTURES_AGENT_DATABASE_URL` | Agent; PG preferred in prod |
| Futures research tables | same as `trades.db` | `FUTURES_RESEARCH_DATABASE_PATH` | Research-on-trades |

**Also on disk (ops snapshots, not code defaults):** `data/trades_before_*.db`.

**`trades.db` hosts:** `schema.sql` trading tables + futures research + market_behavior + strategy_simulator + MTF snapshots + collector diagnostics.

---

## 4. Every PostgreSQL table / usage

| Aspect | Finding |
|--------|---------|
| Driver | `psycopg2` — **no SQLAlchemy / Alembic** |
| Optional for live Polymarket bot | **Yes** (`trades.db` stays SQLite) |
| Optional/required for ME + futures_agent | PG is production option; futures source may **require** PG |
| Shared DB name | typically `trading_ai` (`deploy/docker/docker-compose.yml`) |

### Tables this repo **creates** on PG

- **Futures agent** (`bot/research/futures_agent/schema.py`): `futures_agent_*` (inputs, signals, targets, snapshots, theses, outcomes, telegram bridge, …) + `futures_agent_migrations`
- **Market events**: same large table set as SQLite via `event_schema` + `schema_pg` (~150 tables); live ≤ v65; research v66–72

### Tables this repo **reads** but does not DDL

- `telegram_messages`, `telegram_signals` (external collector; `bot/research/futures/source_reader.py`)

### Ops

- CLI `migrate-to-postgres` / `market-db-copy` — ME SQLite → PG
- `scripts/backup-databases.sh` — optional `pg_dump`

---

## 5. Every module writing trades

### Paper / features / snapshots / decisions (market_events)

| Writer | Tables | Notes |
|--------|--------|-------|
| `signal_paper_performance_s42.open_paper_trades_from_s40` | `market_events_paper_trades_s42` | Open |
| `signal_paper_performance_s42._close_trade` / tick / trailing | paper trades + account | Close / MFE-MAE |
| `portfolio_manager_s55` | (via `_close_trade`) | Replace / stale / hard-cap |
| `trade_intelligence_s55.record_trade_features_on_open` / `finalize_*` | `market_events_trade_features_s55` | **Live** open attribution (S66) |
| `decision_trace_s58.record_decision_on_open` / `finalize_*` | `market_events_trade_decisions_s58` | **Research** via S60 wrappers |
| `trade_postmortem_s56.record_close_snapshot` / `backfill_snapshots` | `market_events_trade_snapshots_s56` | **Research** |
| `history_backfill_s621` | S56 snapshots | Historical import |
| `market_regime_s57.backfill_regimes` | S55 + S56 regime columns | Backfill |
| `signal_learning_s40` | S40 signals / checkpoints / reviews / ops | Learning ingest |

**Orchestration:** `learning-worker` → `run_learning_worker_s40` → `run_paper_performance_cycle_s42`.

### Other trade writers (outside ME paper spine)

| Module | Tables |
|--------|--------|
| `bot/research/ai_analyst/paper_trading/store.py` | `ai_paper_trades_s47` |
| `bot/database.py` | `early_reversion_v2_trades`, etc. |
| `bot/perf/feature_store.py`, `bot/evolution/feature_backfill.py` | `trade_features` (legacy) |
| `bot/portfolio/journal.py` | `live_journal` |
| `bot/strategy/bidirectional_shadow*.py` | shadow trades |

---

## 6. Every module reading trades

### Central loader

`feature_lab_s59.load_lab_trades` — S56 ⟕ S58, fallback S55.

**Consumers:** S61, S62, S62.1, S62.2, S62.3, S63, S64, S64.1, S65, S66.

### Direct readers

| Module | Reads |
|--------|-------|
| `signal_paper_performance_s42` | Open/closed paper, account, reports |
| `portfolio_manager_s55` | Open trades |
| `trade_intelligence_s55` | Feature neighbors |
| `trade_postmortem_s56` | Snapshots / paper for backfill |
| `decision_trace_s58` | Decisions + trades |
| `market_regime_s57` | Closed with regime |
| `history_backfill_s621` | Paper / features / legacy |
| `trade_regression_audit_s55` | Paper + features + S40 |
| `signal_learning_s40` | Paper counts / health |
| `bot/terminal/services/positions_service.py` | Open paper (UI) |
| `bot/research/ai_analyst/...` | Paper + S47 counts |

---

## 7. Every analytics module

Base: `bot/research/market_events/signal_intelligence/` (~170+ modules) + subpackages.

| Purpose | Key modules |
|---------|-------------|
| Paper / portfolio / gate | `signal_paper_performance_s42`, `trade_intelligence_s55`, `portfolio_manager_s55`, `trade_regression_audit_s55` |
| Learning S40 | `signal_learning_s40`, `learning_g2`, `adaptive_learning_g3` |
| Snapshots / decisions / research DB | `trade_postmortem_s56`, `decision_trace_s58`, `research_repository_s60`, `history_backfill_s621` |
| Regime | `market_regime_s57` |
| Feature / filter lab | `feature_lab_s59`, `filter_simulator_s65` |
| Discovery | `strategy_discovery_s61`, `alpha_discovery_s62`, `pattern_discovery_s623`, `pattern_agent_s31`, `pattern_evidence_s32` |
| PnL / drift / intel | `trading_intelligence_report_s621`, `drift_analyzer_s622`, `morning_report_s63`, `pnl_killers_s64`, `long_short_analysis_s641`, `trade_data_audit_s66` |
| Decision engine S20 | `decision_engine_s20/`, `explain_decision_s22`, `signal_inbox_s23` |
| G3 pipeline | `runner_g3`, `candidate_g31`, `shadow_g40`, `experimental_g39`, `validation_signal_s11` |
| Telegram | `telegram_f*`, `telegram_g*`, `telegram_diagnostics_s631` |
| News / narrative / multi-source | `news_intelligence/`, `narrative_engine/`, `multi_source/`, `event_intelligence/` |
| Quant / lake | `quant_research_g50`, `research_lake_g51`, `research_terminal_s50`, `audit_engine_s51` |

**Outside SI:** `bot/analytics.py`, `bot/report`, `bot/optimizer`, `bot/research/strategy_simulator`, `bot/research/mtf`, `bot/research/market_behavior`, `bot/scientist`, `bot/evolution`.

---

## 8. Every report generator

### Writes under `research/reports/**`

| CLI | Output |
|-----|--------|
| `intelligence-report` | `research/reports/intelligence/latest.{md,json}`, `ai_context.md` |
| `explain-drift` | `…/intelligence/drift_report.{md,json}` |
| `discover-patterns` | `…/intelligence/patterns.{md,json}`, `pattern_candidates.md` |
| `morning-report` | `research/reports/morning/latest.{md,json}` |
| `pnl-killers` | `research/reports/pnl/pnl_killers.{md,json}` |
| `simulate-filters` | `…/pnl/filter_simulator.{md,json}` |
| `long-short-analysis` | `…/pnl/long_short_analysis.{md,json}` |
| `audit-trade-data` | `…/pnl/data_quality.{md,json}` |
| `telegram-health-report` | `research/reports/system/telegram_health.{md,json}` |

### Other report sinks

| CLI / path | Output |
|------------|--------|
| `feature-lab`, `strategy-discovery`, `alpha-discovery`, `trade-postmortem`, `market-regime`, `decision-report`, `paper-performance`, … | Mostly **stdout** (+ DB rows) |
| `cli-architecture-audit` | `docs/research/S60_1_CLI_ARCHITECTURE_AUDIT.md` |
| `news-intel-reports` / narrative | `bot/research/market_events/reports/` |
| F-series report CLIs | Mostly print via `reports.py` / ops report helpers |
| `bot.report` / `bot.analytics` | Polymarket settled-trade reports |

---

## 9. Every migration

| System | Location | Version table | Range |
|--------|----------|---------------|-------|
| Trades bot | `schema.sql` + `bot/database.py` `_apply_migrations` | **None** (idempotent ALTERs) | Live SQLite |
| Market events live | `event_schema.apply_migrations` | `market_events_migrations` | **v1–v65** |
| Market events research | `research_repository_s60.apply_research_migrations` | `market_events_research_migrations` | **v66–v72** |
| Futures agent | `futures_agent/schema.py` | `futures_agent_migrations` | stages **1–7** |
| Futures / MB / SS / MTF | `ensure_tables` helpers | None | Create-if-not-exists |

**Constants:** `LIVE_SCHEMA_VERSION = 65`, research watermark `SCHEMA_VERSION / RESEARCH_SCHEMA_VERSION = 72`.

**Absent:** Alembic, SQLAlchemy, `migrations/` SQL folders.

### Research migrations (v66–v72) — HIGH

| Ver | Stage | Tables |
|-----|-------|--------|
| 66 | S56 | snapshots, postmortem runs, rule suggestions, ops |
| 67 | S57 | regime runs/ops |
| 68 | S58 | trade decisions |
| 69 | S59 | feature lab |
| 70 | S60 | research ops |
| 71 | S61 | strategy discovery |
| 72 | S62 | alpha discovery |

---

## 10. Every command that is never called

**Definition used:** never referenced outside `bot/research/market_events/__main__.py` in `bot/`, `tests/`, `docs/`, `scripts/`, `deploy/` (string heuristic). **MEDIUM confidence** — humans may still run them manually.

### Unreachable due to duplicate registration (HIGH)

| Command | Issue |
|---------|-------|
| `validation-report` | Listed **twice** in `choices=`. First dispatch = G4 auto-validation; second (S11) handler is **dead**. |

### Registration-only clusters (~82 commands) — MEDIUM

Early shock audits (`shock-*-audit/report`, `polymarket-paper-audit`, …), many F/G report CLIs (`live-dashboard`, `whale-report`, …), G2–G5 diagnostics (`shadow-*`, `quant-report`, …), news one-shots (`news-latest`, …), research AI (`research-ai`, `research-cost`, …), debug (`pipeline-audit`, `sqlite-lock-*`, …).

**Not the same as “safe to remove”.** These are often intentional one-shot tools.

### Scheduler-wired (ACTIVE — not orphans)

`shock-paper-run`, `observe-run`, `ai-worker-run`, `g3-run`, `learning-worker`, `news-intel-worker`, `event-engine-run`, `multi-source-run`, `narrative-engine-run`, `dashboard-api-serve`, plus `start-all` / `stop-all` / `status` / `doctor` / …

### Tests-only / docs-thin but implemented (USED as CLI)

`explain-drift`, `discover-patterns`, `pnl-killers`, `simulate-filters`, `long-short-analysis`, `audit-trade-data`, `backfill-history`, `morning-report`, `telegram-health-report`, …

---

## 11. Every Python file never imported

### Confirmed orphan (HIGH)

| File | Notes |
|------|-------|
| `bot/research/market_events/signal_intelligence/analyze_symbol_g36.py` | **0 importers.** Docstring claims `/analyze SYMBOL`; router uses `research_terminal_s50` instead. |

### CLI-only thin wrappers (USED via CLI, not dead)

`cli_architecture_audit_s601.py`, `filter_simulator_s65.py`, `history_backfill_s621.py`, `long_short_analysis_s641.py`, `morning_report_s63.py`, `research_stress_s60.py`, `telegram_diagnostics_s631.py`, `trade_data_audit_s66.py`, `pnl_killers_s64.py`, …

### Needs review (MEDIUM)

| File | Notes |
|------|-------|
| `telegram_vision_g36.py` | Tests + definitions; production photo path may use S50 intake instead |

Heuristic limits: dynamic `importlib` can under-count. Full SI tree was sampled; not every `bot/` file outside ME was exhaustively import-graph verified.

---

## 12. Every duplicate responsibility

| Concern | Overlap |
|---------|---------|
| PnL / WR / PF / expectancy math | Near-copies in S56, S57, S59, S62.1, S62.3, S64 |
| Coin / direction / hour ranking | S62.1 intelligence, S63 morning, S64.1 long-short, postmortem deep stats |
| Regime | Live gate S57 + offline S57 report + re-summaries in intel/morning |
| Pattern / rule discovery | S61 strategy discovery, S62 alpha, S62.3 patterns, older S31/S32 |
| Filter ON/OFF simulation | S59 feature lab vs S65 filter simulator vs S61/S62 predicates |
| Drift / regression | S62.2 drift vs S55.2 regression vs intel `performance_drift` |
| PnL loss diagnosis | S64 killers vs S56 RCA vs regression root-cause |
| Snapshot population | Live close (`record_close_snapshot`) vs `backfill_snapshots` vs `history_backfill_s621` |
| Process control | `prod-*.sh` vs `market_events start-all` (both can touch telegram) |
| Report CLI name collisions | `report` / `status` / `telegram-status` across packages |
| Near-duplicate commands | `history-backfill` vs `backfill-history`; `near-miss-report` vs `shock-near-miss-report`; `db-info` vs `market-db-info` |

---

## 13. Every obsolete experiment

| Item | Status |
|------|--------|
| G39 experimental calibration | Still hooked from `runner_g3` — **experimental but live-wired** |
| G40 / G401 shadow lane | Same — research/shadow, not deleted |
| SHOCK_F / F_v2 shadow audits | CLI + (v2) historical replay |
| Phase E shock report CLIs | Pre-G3 leftovers; still registered |
| Polymarket V4 / YES_C / NO_C / bidirectional | Separate product line; docs say **NO-GO** for live expansion |
| Scientist experiments | PROJECT_CONTEXT: historically rejected/failed |
| `.cursor/PROJECT_CONTEXT.md` | **Stale** vs current market_events SI reality (docs drift) |
| S41–S44 as filenames | Logic lives in packages (`news_intelligence`, `event_intelligence`, `multi_source`) — not obsolete |

---

## 14. Every feature marked Sxx (and related) — still used?

### S40–S66

| Stage | Primary module(s) | Used? |
|-------|-------------------|-------|
| S40 | `signal_learning_s40.py` | **USED** — `learning-worker` |
| S41 | `news_intelligence/*` | **USED** — `news-intel-worker` |
| S42 | `signal_paper_performance_s42.py` | **USED** — paper spine |
| S43 | `event_intelligence/*` | **USED** — `event-engine-run` |
| S44 | `multi_source/*` | **USED** — `multi-source-run` |
| S50 | `claude_channel_s50`, `research_terminal_s50`, artifacts/intake | **USED** — Telegram + CLI |
| S51 | `audit_engine_s51.py` | **USED** — `research-audit` |
| S53/S54 | embedded in S42 | **USED** |
| S55.1 | `trade_intelligence_s55.py` | **PARTIAL** — via S42 / S66 attribution; no dedicated CLI |
| S55.2 | `trade_regression_audit_s55.py` | **USED** — CLI only |
| S55.3 | `portfolio_manager_s55.py` | **PARTIAL** — via S42 |
| S56 | `trade_postmortem_s56.py` | **USED** |
| S57 | `market_regime_s57.py` | **USED** |
| S58 | `decision_trace_s58.py` | **USED** |
| S59 | `feature_lab_s59.py` | **USED** |
| S60 | `research_repository_s60.py`, `research_stress_s60.py` | **USED** |
| S60.1 | `cli_architecture_audit_s601.py` | **USED** — CLI only |
| S61 | `strategy_discovery_s61.py` | **USED** |
| S62 | `alpha_discovery_s62.py` | **USED** |
| S62.1 | `trading_intelligence_report_s621.py`, `history_backfill_s621.py` | **USED** |
| S62.2 | `drift_analyzer_s622.py` | **USED** |
| S62.3 | `pattern_discovery_s623.py` | **USED** |
| S63 | `morning_report_s63.py` | **USED** — CLI |
| S63.1 | `telegram_diagnostics_s631.py` | **USED** — CLI |
| S64 | `pnl_killers_s64.py` | **USED** |
| S64.1 | `long_short_analysis_s641.py` | **USED** — CLI |
| S65 | `filter_simulator_s65.py` | **USED** — CLI |
| S66 | `trade_data_audit_s66.py` (+ open attribution in S55/S58/S56) | **USED** |

### Related stages (summary)

| Tag | Status |
|-----|--------|
| F0–F7 / F71–F73 | **USED** via `hooks` / paper runner |
| G0–G5 | **USED** (`g3-run` supervisor) |
| G39 / G40 | **LEGACY/EXPERIMENTAL but USED** |
| G36 `analyze_symbol` | **UNUSED** |
| N11 | **USED** (news collector) |
| E1–E5 | **LEGACY docs**; many E CLIs still registered |
| S11 / S21–S23 / S31–S32 | **USED** or **PARTIAL** |

---

# Classification sections

## ACTIVE

Systems that are supervisor-wired, on the paper trade spine, or clearly production/ops.

| Item | Why |
|------|-----|
| `python -m bot.main` | Production Polymarket loop |
| `bot.ops` + `scripts/prod-*.sh` | Prod process control |
| `futures_agent telegram-poll` | Telegram intake |
| `market_events start-all` fleet (12 workers) | Research/live ME supervisor |
| F0–F7 + G3 (`runner_g3`, `hooks`, `paper_runner`) | Core signal → paper path |
| S40 / S42 / S55 / S58 / S56 / S60 | Trade write/read spine |
| S41 / S43 / S44 / S50 / S51 | News / events / multi-source / AI terminal |
| S57–S59, S61–S66 CLIs | Current research analytics |
| Live DB migrations ≤65; research migrations 66–72 | Current schema |

---

## LEGACY

Still present / sometimes imported; superseded or research-only / NO-GO for live.

| Item | Why |
|------|-----|
| Phase E shock audit/report CLIs | Pre-G3; keep for audits |
| G39 experimental / G40 shadow | Wired but experimental lanes |
| Polymarket V4 / YES_C / NO_C / bidirectional research | Separate stack; docs discourage live expansion |
| Scientist / old PROJECT_CONTEXT narrative | Historical; docs drift |
| Older pattern agents S31/S32 vs S61–S623 | Older SELECT-only pattern tooling |
| Duplicate analytics implementations | Functional overlap; not deleted |

---

## UNUSED

| Item | Confidence | Evidence |
|------|------------|----------|
| `analyze_symbol_g36.py` | HIGH | Zero importers; replaced by S50 `/analyze` |
| Second `validation-report` dispatch (S11 handler) | HIGH | Unreachable after duplicate `choices=` |
| Many registration-only CLIs *as scheduled jobs* | MEDIUM | Never referenced outside `__main__.py` (may still be manual) |

---

## SAFE TO REMOVE

**Candidates only — verify with ops before deletion.**

| Candidate | Risk | Notes |
|-----------|------|-------|
| `analyze_symbol_g36.py` | Low | Clear orphan |
| Duplicate `validation-report` entry in `choices=` | Low | Fix registration; keep one intentional handler |
| Bulk deletion of ~82 CLI-only commands | **High** | Would remove only entrypoints to many research modules |

**Do not** remove S55–S66, supervisor workers, or S40–S42 without a dedicated cleanup phase.

---

## NEEDS REVIEW

| Item | Question |
|------|----------|
| Dual telegram supervisors | `prod-control` vs `start-all` — who owns telegram in prod? |
| `validation-report` duplicate | Which handler should remain (G4 vs S11)? |
| CLI-only S63–S66 / shadow / debug | Keep as manual toolkit or fold into fewer commands? |
| `telegram_vision_g36` vs S50 photo intake | Dead path or alternate? |
| S55.1 / S55.3 without dedicated CLI | Intentional side-effects only? |
| Snapshot triple writers | Unify close / backfill / history rules? |
| Duplicate PnL metric helpers | Extract shared library in a later phase? |
| PG vs SQLite for ME in this environment | Confirm which backend is actually running |
| `.cursor/PROJECT_CONTEXT.md` | Update or archive to stop agent drift |
| Futures_agent’s large CLI surface | Likely similar orphan pattern; out of Phase 1 depth |
| Production inventory docs vs market_events fleet | Docs under-describe ME supervisor |

---

## Recommended Phase 2 cleanup order (advisory only)

1. Fix `validation-report` duplicate registration.  
2. Delete or archive `analyze_symbol_g36.py`.  
3. Document ownership of telegram process control.  
4. Tag CLI commands as `supervisor` / `manual` / `debug` in help text (no behavior change).  
5. Later: consolidate PnL metric helpers and snapshot writers.

---

## Appendix A — Mental model

```
LIVE Polymarket     → SQLite data/trades.db
LIVE market_events  → SQLite data/market_events.db  OR PG trading_ai  (≤ v65)
RESEARCH ME (S60)   → data/market_events_research.db OR PG             (v66–72)
FUTURES AGENT       → SQLite data/futures_agent.db  OR PG              (stages 1–7)
FUTURES SOURCE      → PG telegram_* (read-only, external)
```

**Paper analytics spine**

```
S40 ingest → S42 open → S55 features + S58 decision
         → tick/close → S55 finalize + S56 snapshot (research)
         → load_lab_trades → S59–S66 reports
```

---

## Appendix B — Totals

| Metric | Count |
|--------|-------|
| `market_events` choices entries | 195 |
| `market_events` unique commands | 194 |
| Exact CLI name duplicates | 1 (`validation-report`) |
| `start-all` services | 12 |
| `__main__.py` packages under `bot/` | ~20 |
| `futures_agent` commands | 44 |
| First-class SQLite DB roles | 4–5 |
| ME live migration ceiling | v65 |
| ME research migrations | v66–v72 |
| Confirmed SI orphan modules | 1 |
| Registration-only ME CLIs (heuristic) | ~82 |

---

*End of Phase 1 Architecture Audit. No application code was modified.*
