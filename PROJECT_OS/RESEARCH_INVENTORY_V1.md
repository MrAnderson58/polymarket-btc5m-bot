# Research Inventory V1

**Date:** 2026-07-28  
**Branch:** `develop-terminal`  
**Scope:** Audit of existing analytics — what knowledge the system can already extract.  
**Constraint:** No strategy / gate / threshold changes in this document (inventory only).

**Evidence sample (this Mac analytics DB):** `data/market_events.db` — **42** closed S55 trades (`research-sync-doctor`). Mini production corpus (~2162 closes) may differ; re-run Pack01 after Research Sync on the Mini DB.

---

## 1. Data sources

### 1.1 Database topology

| DB | Typical path | Role |
|----|--------------|------|
| Live / analytics | `data/market_events.db` | Collectors, G3, S40/S42 paper, **S55 gate features**, TI `ti_*` |
| Research | `data/market_events_research.db` | S56–S62 discovery (postmortem, feature-lab, strategy/alpha) |
| Sync install | `data/research_sync/market_events.db` | Last imported Research Sync snapshot |
| Polymarket legacy | `data/trades.db` | Separate stack (not futures S55 spine) |

Export / `trade-statistics` share `resolve_research_analytics_sqlite_path()` (override: `MARKET_EVENTS_RESEARCH_ANALYTICS_DB`).

### 1.2 Tables — Trading Core (live path)

| Table | Used by |
|-------|---------|
| `market_events` | Shock detection |
| `market_event_snapshots` | Per-event OB / funding / OI |
| `market_snapshots_g3` | Live microstructure → S55 enrichment |
| `market_events_paper_trades_s42` | Futures paper journal |
| `market_events_paper_account_s42` / `_reports_s42` | Paper equity / reports |
| `market_events_trade_features_s55` | Entry features + gate + close outcomes (**similarity pool**) |
| `market_events_signal_learning_s40_*` | Signal learning ops |
| `market_events_shadow` | Detector A/B shadow |
| `ti_*` / `ti_paper_knowledge` | Trade Intelligence knowledge |

Live DDL watermark for gate features: **S55** (`LIVE_SCHEMA_VERSION=66`).

### 1.3 Tables — Research Pack / discovery (mostly research DB)

| Table | Stage |
|-------|-------|
| `market_events_trade_snapshots_s56` | Postmortem snapshots |
| `market_events_postmortem_*_s56` | RCA / suggestions |
| `market_events_regime_*_s57` | Regime report runs |
| `market_events_trade_decisions_s58` | Decision traces |
| `market_events_feature_lab_*_s59` | Filter ON/OFF lab |
| `market_events_strategy_discovery_*_s61` | Composed filters |
| `market_events_alpha_discovery_*_s62` | Pattern miner |

### 1.4 Features — collected vs computed (S55 open)

**Source:** `signal_intelligence/trade_intelligence_s55.py` → `build_entry_features` / `_NUMERIC_DIMS`.

| Feature | At open | Kind |
|---------|---------|------|
| `symbol`, `direction`, `strategy` | From S40 | Collected |
| `hour`, `weekday`, `session` | From timestamp | Computed |
| `funding`, `atr`, `volatility`, `volume`, `fear_greed`, `trend` | S40 / G3 | Collected |
| `oi_delta` | Δ OI (or raw OI fallback) | Computed |
| `funding_sign` | sign(funding) | Computed |
| `news_score`, `ai_score` / `confidence` | S40 snapshot | Collected |
| `btc_dominance` | G3 best-effort | Collected |
| `market_regime` | S57 attach | Computed |
| `rsi`, `etf_flow`, `macro_score`, `spread`, `shock_score` | Always `None` in builder | Slot only (orphan) |
| Close: `pnl_*`, MFE/MAE, TP/SL flags | On close | Outcome |

---

## 2. All existing research modules

Flow pattern for each: **name → inputs → conclusions → save location → consumer**.

### 2.1 Expectancy Intelligence V1

```
Expectancy breakdown / feature-importance / similar-trades /
counterfactual / daily-intelligence / dataset-audit
↓
reads market_events_trade_features_s55 (+ neighbors)
↓
stdout; daily → reports/daily/YYYY-MM-DD.md; knowledge → ti_paper_knowledge
↓
Human only (no gate writes)
```

| Module | CLI | Conclusions |
|--------|-----|-------------|
| Expectancy breakdown | `expectancy-breakdown` | Why NEGATIVE_EXPECTANCY (WR / loss size / sample / variance) |
| Feature importance | `feature-importance` | Pearson corr vs pnl / win / EV |
| Similar trades | `similar-trades` | Neighbor inspection for a symbol |
| Counterfactual | `counterfactual` | Neighbor proxy if expectancy filter ignored |
| Daily intelligence | `daily-intelligence` | Day activity + EV + playbook snippets |
| Dataset audit | `dataset-audit` | Missing % / uniqueness / corr feasibility |

### 2.2 Research Pack 01

```
Trade Statistics
↓
reads closed S55 (same analytics DB as Research Sync)
↓
builds aggregate stats, quantile buckets, pairs, winner/loser, playbook
↓
reports/research/{statistics.md,bucket_analysis.csv,pair_analysis.csv,market_playbook.md}
↓
Human only
```

CLI: `trade-statistics`. Reliability flag: **n ≥ 30**.

### 2.3 Gate / regime diagnostics

```
Gate funnel (aliases: paper-gate-funnel, regime-report)
↓
S55 gate_decision rows + F&G closed buckets
↓
UNIFIED GATE FUNNEL + FEAR & GREED RESEARCH
↓
stdout — Human (does not change filters)
```

```
market-regime
↓
S55 features / closed outcomes
↓
Regime × direction EV / PF
↓
ops tables + stdout — S57 gate uses live classify; CLI is report
```

### 2.4 S56–S62 discovery (research DB)

| Module | CLI | Input | Output save | Consumer |
|--------|-----|-------|-------------|----------|
| Trade postmortem | `trade-postmortem` | S56 snaps | Research S56 tables | Human / suggestions |
| Feature lab | `feature-lab` | S56→S55 | `feature_lab_*_s59` | Human (never auto-apply) |
| Strategy discovery | `strategy-discovery` | Lab trades | `*_s61` | Human |
| Alpha discovery | `alpha-discovery` | Lab trades | `*_s62` | Human |
| Intelligence report | `intelligence-report` | Aggregates | `research/reports/intelligence/` | Human |
| Morning report | `morning-report` | Multi-table | `research/reports/morning/` | Human |
| PnL killers | `pnl-killers` | Closed features | `research/reports/pnl/` | Human |
| Simulate filters | `simulate-filters` | Historical | stdout | Human |
| Long/short analysis | `long-short-analysis` | Closed | stdout | Human |

### 2.5 Trade Intelligence + Sync

```
trade import|list|report|similar
↓
ti_* envelope
↓
Human / future TI V2

research-sync-export|import|status|doctor
↓
SQLite snapshot tar + SHA256
↓
Cross-machine analytics parity
```

### 2.6 Adjacent (older packs)

`performance`, `quant-research`, G4 `false-rejects` / `optimizer-report`, shock / historical replay reports — mostly human; G4 can feed threshold validation (not S55).

---

## 3. What actually influences strategy

| Feature | Used in decision? | Analytics only? |
|---------|:-----------------:|:---------------:|
| `trend` | **Yes** (S57 regime + S55 similarity) | Also Pack01 / EI |
| `fear_greed` | **Yes** (S57 + similarity) | Also Pack01 / funnel |
| `funding` / `funding_sign` | **Yes** (S57 + similarity) | Also Pack01 |
| Neighbor EV (`expected_pnl_pct`) | **Yes** (S55 expectancy gate) | Counterfactual / breakdown |
| `similar_count` / min similar | **Yes** (cold-start vs block) | Dataset audit |
| Max open / S55 enabled | **Yes** (risk gate) | Funnel |
| `market_regime` label | **Yes** (S57 stats gate) | Playbook / Pack01 |
| `hour`, `weekday` | **Yes** (similarity only) | EI importance |
| `volatility`, `atr`, `volume`, `oi_delta` | **Yes** (similarity) | Pack01 |
| `ai_score`, `news_score`, `btc_dominance` | **Yes** if non-null (similarity) | Pack01 / EI |
| Pack01 buckets / playbook | **No** | **Yes** |
| Expectancy Intelligence reports | **No** | **Yes** |
| S59–S62 discovery | **No** (suggestions only) | **Yes** |
| `rsi`, `etf_flow`, `macro_score`, `spread`, `shock_score` | **No** (null at open) | Declared dims / sparse labs |

**Decision surface (narrow):**  
Collectors → G3/S40 → S55 features → **S57 regime** → **S55 similar-trade EV** → open/reject → S42 paper.

---

## 4. Features collected but unused (or unused well)

### 4.1 Slot collected / declared, never filled at open

| Feature | Issue |
|---------|--------|
| RSI | In `_NUMERIC_DIMS`, always `None` in builder |
| ETF flow | Same |
| Macro score | Same |
| Spread | Same |
| Shock score | Same |

→ Dilute schema and audits; skip in similarity when null.

### 4.2 Collected / in similarity, but weak on current sample

| Feature | Issue (Pack01 n=42) |
|---------|---------------------|
| Fear & Greed | Constant buckets (~22) — no discriminative power |
| Funding | Constant / suspicious scale (~54.1) — data quality |
| Trend / Volatility | Flat across quantiles |

### 4.3 Analytics analyzes; strategy does not read reports

Funding / OI / Trend / F&G / AI / Confidence appear in Pack01 & EI, but **reports never write back into gate**. Strategy only sees live feature vector + neighbor EV.

### 4.4 Volume spike

Volume is a similarity dim and Pack01 bucket (“Volume” via `volume` / vol features). There is **no dedicated Volume Spike detector** wired into S55 gate. Research packs do not isolate “spike vs no-spike” as a first-class predicate on this DB.

---

## 5. Project map

```
Collectors (Bybit / G3 / news / S40 signals)
        ↓
   SQLite live (market_events.db)
        ↓
   Features (S55 build_entry_features + S57 regime)
        ↓
   Paper Trade (S42 open/close; gate: REGIME → EXPECTANCY → RISK)
        ↓
   Research (Pack01, EI, funnel, S56–S62 on research DB)
        ↓
   Reports (reports/research, reports/daily, research/reports/*)
        ↓
   Knowledge (ti_*, ti_paper_knowledge; Research Sync snapshots)
```

Parallel: Research Sync export/import keeps analytics SHA identical across Mini / MacBook.

---

## 6. What is proven / unproven / promising / removable

**Caveat:** Conclusions below are for **this Mac sample (n=42 closed)**. Re-evaluate after Mini sync (~2162).

### 6.1 Statistically useful (on this sample)

| Finding | Evidence |
|---------|----------|
| **Overall expectancy is negative** | Pack01: EV −1.52%, CI95 excludes 0; PF 0.37; WR 35.7% |
| **S55 NEGATIVE_EXPECTANCY gate is active** | Funnel / ops: many rejects without `paper_trade_id` |
| **MFE &lt; |MAE| asymmetry** | Avg MFE 2.0% vs MAE −2.4% — adverse selection / exits |

These are system-level outcomes, not proof that any single feature “works”.

### 6.2 Not proven useful (yet)

| Feature / idea | Why |
|----------------|-----|
| Fear & Greed as alpha | Constant / low variance in Pack01 |
| Funding as alpha | Constant / bad scale on sample |
| Quantile playbook combos | No **n≥30** reliable rows |
| Pair Top-20 | Same reliability floor unmet |
| Orphan dims (rsi, etf, macro, spread, shock) | Never populated at open |

### 6.3 Promising but sample too small

| Item | Why promising | Blocker |
|------|---------------|---------|
| Regime × direction EV (S57) | Already decision-linked; needs evidence ≥30 | Need Mini corpus |
| Trend × Funding / OI playbook | Structure exists; low-N clusters only | n&lt;30 |
| Neighbor EV as filter | Live gate uses it; counterfactual not validated on large set | Sync Mini DB first |
| Dataset completeness vs decision quality | `dataset-audit` can show missing % | Must run on production DB |

### 6.4 Candidates to remove or demote (schema / dims — **not** live code yet)

| Candidate | Action proposal (roadmap only) |
|-----------|--------------------------------|
| `rsi`, `etf_flow`, `macro_score`, `spread`, `shock_score` as similarity dims | Stop listing until collector fills them |
| Constant F&G / funding in analytics | Fix ingest / scale before more bucket reports |
| Duplicate CLIs that only print empty labs on hist DB | Deprecate after inventory of actual usage |

**Do not delete columns or change gates without a dedicated task.**

---

## 7. Research Roadmap (5–10 steps, no code)

1. **Sync Mini corpus** — On Mini: `research-sync-doctor` → set `MARKET_EVENTS_RESEARCH_ANALYTICS_DB` to DB with ~2162 closes → `research-sync-export` → Mac `import --activate` → confirm `SYNCED` and matching `trade-statistics`.
2. **Re-run Pack01 + dataset-audit on synced DB** — Require n≥30 buckets; discard constant-feature buckets as “not informative”.
3. **Feature quality gate** — Document missing % and uniqueness for every `_NUMERIC_DIMS` key; freeze orphan slots.
4. **Validate neighbor EV** — `counterfactual --hours 168` on Mini traffic: did blocked trades lose money? Quantify false rejects vs true saves.
5. **Regime evidence refresh** — Rebuild S57 regime×direction EV/PF with Mini n; keep block rules unchanged until report signed off.
6. **Funding / F&G ingest audit** — Trace G3 → S40 → S55 for scale and constancy; fix data before interpreting alpha.
7. **Winner vs loser on n≥200** — Re-run Pack01 Part 4; only then propose (human) filter hypotheses.
8. **Feature-lab on research DB** — S59 ON/OFF Δ expectancy for funding / F&G / regime — still observe-only.
9. **Kill-or-fill orphans** — Decide: implement collectors for rsi/etf/macro/spread/shock **or** remove from similarity contract in a later schema task.
10. **Strategy change freeze** — Any gate/threshold change only after written evidence from steps 1–8 and an explicit shipping task.

---

## Appendix — Key paths

```
bot/research/market_events/signal_intelligence/trade_intelligence_s55.py
bot/research/market_events/signal_intelligence/market_regime_s57.py
bot/research/market_events/expectancy_intelligence/
bot/research/market_events/research_pack_01/
bot/research/market_events/research_sync_v1.py
bot/research/market_events/trade_intelligence/
reports/research/
PROJECT_OS/DATABASE_INVENTORY.md
PROJECT_OS/FILTER_PIPELINE.md
```

## Appendix — Sample Pack01 headline (this Mac)

- Total 42 · WR 35.7% · EV −1.52% · PF 0.37  
- No reliable playbook rows (n≥30)  
- Export path = analytics path = `…/data/market_events.db`
