# PROJECT_OS — Filter Pipeline Recovery

_Documentation only. Recovered from implemented Python modules. No code or data modified._

## Scope

Every **entry filter**, **shadow filter**, **paper gate**, **research ON/OFF / remove simulator**, and **threshold profile** found in the repo.

| Class | Blocks live / paper opens? | Notes |
|-------|----------------------------|-------|
| Live Polymarket | Yes (NO_C adaptive) | Can skip entry |
| Shadow / observe | No | Stats only |
| Paper market-events (S55/S57) | Yes (paper) | Gates `open_paper_trades_from_s40` |
| G3.1 production thresholds | Yes (candidates) | Production candidate path |
| G39 / G40 | Experimental / shadow | Not production default |
| S59 / S61 / S62 / S65 | No | Research / advisory |
| Optimizer / evolution | Offline / shadow | Grid & promotion verdicts |

---

## Execution order

### A. Polymarket live (ER v2 / NO_C)

```
signal / ask checks
  → NO_C Adaptive BTC Filter (resolve_no_c_filter)     [BLOCKS if STRICT + ask too high]
  → NO_C Filter Shadow (record_no_c_filter_shadow)     [NEVER blocks]
  → attempt_entry_open + live counters
```

### B. Market-events paper open (futures / signal learning)

```
G3.1 candidate thresholds (production) / optional G39–G40
  → S40 signal ingest
  → S42 open_paper_trades_from_s40
       → build_entry_features
       → should_open_trade:
            1) S57 apply_regime_gate          [first]
            2) S55 disabled / max-open / similar / expected PnL
       → maybe_replace_weakest_for_candidate  [if at capacity]
       → open paper + S55 features + S58 decision (research)
```

### C. Bidirectional V1.2 shadow

```
evaluate_direction(SHADOW_V12_ENTRY_CONFIG)
  → passes_v12_filters                        [blocks shadow open]
  → shadow open / reject (v12_filter_reject)
```

### D. Offline research (lab trades)

```
load_lab_trades (S56 ± S58 / S55)
  → S59 feature-lab (ON/OFF predicates)
  → S61 strategy-discovery / S62 alpha-discovery (hypothesis / pattern book)
  → S64 pnl-killers
  → S65 simulate-filters (remove segments) → research/reports/pnl/filter_simulator.*
```

### E. Strategy simulator (Polymarket 5m paths)

```
list_filtered_market_paths(MarketFilter)
  → discovery / walk-forward / engine
  → gate_on_validation (min trades / EV)
```

---

## Filter catalog

### 1. NO_C Adaptive BTC Filter (live)

| Field | Value |
|-------|-------|
| **Name** | NO_C Adaptive BTC Filter |
| **Symbols** | `resolve_no_c_filter`, `resolve_no_c_entry_threshold`, `NoCFilterDecision` |
| **Python file** | `bot/no_c_btc_filter.py` |
| **Purpose** | When BTC rose hard over 30s, tighten NO_C ask threshold (STRICT); otherwise NORMAL. Skip entry if ask exceeds strict threshold. |
| **Input** | `sqlite3.Connection` (`market_checks` BTC history); `current_btc`; `now_ts`; strategy name |
| **Output** | `NoCFilterDecision(mode, btc_move, threshold)`; may skip with `STRICT_PRICE`; counters in `no_c_filter_live_counters` |
| **Parameters** | `LOOKBACK_SEC=30`; `BTC_MOVE_STRICT_USD=5.0`; `NO_C_NORMAL_THRESHOLD=0.40`; `NO_C_STRICT_THRESHOLD=0.36`; thresholds via `effective_entry_threshold` (+ `ER_ENTRY_PRICE_OFFSET`) |
| **Where used** | `bot/early_reversion_v2.py` open path; `bot/er_entry_check.py`; `bot/main.py` (`log_no_c_filter_live_config`); reports (`format_no_c_filter_live_block`) |
| **Execution order** | Live path step 2 — after ask available, before entry open |

---

### 2. NO_C Filter Shadow (observe-only)

| Field | Value |
|-------|-------|
| **Name** | NO_C BTC-up Filter Shadow |
| **Symbols** | `record_no_c_filter_shadow`, `FilterShadowHit`, `compute_btc_moves` |
| **Python file** | `bot/no_c_filter_shadow.py` |
| **Purpose** | Grid of lookback × USD thresholds; logs WOULD_BLOCK without blocking entries. |
| **Input** | conn; `market_slug`, `entry_ts`, `entry_price`, `current_btc`; reads `market_checks` |
| **Output** | `list[FilterShadowHit]`; tables `no_c_filter_shadow_counters`, `no_c_filter_shadow_entries`; report formatter |
| **Parameters** | `LOOKBACK_SECS=(30,60,90)`; `THRESHOLDS_USD=(5,10,15,20)`; `MAX_BTC_LOOKUP_DELTA_SEC=10`; block rule `move_usd > threshold`; env `ENABLE_NO_C_FILTER_SHADOW` default **True** (`bot/config.py`) |
| **Where used** | `early_reversion_v2.py` after live filter allows path; `bot/main.py`; `bot/report/builder.py` |
| **Execution order** | Live path step 3 — after adaptive filter; **never gates** |

---

### 3. S57 Regime Gate (paper)

| Field | Value |
|-------|-------|
| **Name** | Market Regime Gate |
| **Symbols** | `apply_regime_gate`, `attach_regime_to_features` |
| **Python file** | `bot/research/market_events/signal_intelligence/market_regime_s57.py` |
| **Purpose** | Classify regime; if enough regime×direction history and expectancy + PF both bad → block paper open. |
| **Input** | conn; entry `features` dict (regime attached if missing) |
| **Output** | `(ok, reason, meta)` — `REGIME_BLOCK` / `REGIME_PASS` / `REGIME_COLD` / `REGIME_DISABLED`; ops table `market_events_regime_ops_s57` |
| **Parameters** | `S57_ENABLED=True`; `S57_FILTER_ENABLED=True`; `S57_MIN_EVIDENCE=30`; `S57_MIN_EXPECTANCY=0.0`; block if expectancy &lt; MIN **and** PF &lt; 1.0 (non-inf); env `S57_*` |
| **Where used** | Called first inside `should_open_trade`; CLI `market-regime` |
| **Execution order** | Paper open gate **#1** inside S55 |

---

### 4. S55 Trade Intelligence Gate (paper)

| Field | Value |
|-------|-------|
| **Name** | S55 Should-Open Gate |
| **Symbols** | `should_open_trade` |
| **Python file** | `bot/research/market_events/signal_intelligence/trade_intelligence_s55.py` |
| **Purpose** | Gate paper opens: regime (delegated), max-open, similar-history expected PnL. |
| **Input** | conn; `features`; `open_count`; `skip_max_open_check` |
| **Output** | `(allow, gate_decision, estimate)` — `ALLOWED`, `INSUFFICIENT_HISTORY`, `MAX_OPEN`, `NEGATIVE_EXPECTANCY`, `DISABLED`, `REGIME_BLOCK` |
| **Parameters** | `S55_ENABLED=True`; `S55_MAX_OPEN_TRADES=25`; `S55_SIMILAR_K=100`; `S55_MIN_SIMILAR=20`; `S55_MIN_EXPECTED_PNL_PCT=0.0`; env `S55_*` |
| **Where used** | `signal_paper_performance_s42.open_paper_trades_from_s40`; learning-worker / paper-performance CLI |
| **Execution order** | Paper open: after S57, before portfolio replace / open |

---

### 5. S55 Portfolio Replace Gate (paper)

| Field | Value |
|-------|-------|
| **Name** | Portfolio Replace Gate |
| **Symbols** | `maybe_replace_weakest_for_candidate` |
| **Python file** | `bot/research/market_events/signal_intelligence/portfolio_manager_s55.py` |
| **Purpose** | At max open, free weakest position if candidate expected PnL beats it by edge. |
| **Input** | conn; `candidate_expected_pnl_pct`; `open_count`; `max_open` |
| **Output** | `{replaced, freed, weakest_id, …}`; exit reasons `PORTFOLIO_REPLACE`, `STALE` |
| **Parameters** | `S55_PORTFOLIO_REPLACE_ENABLED=True`; `S55_MIN_REPLACE_EDGE_PCT=0.05`; `S55_STALE_MULTIPLIER=1.0` |
| **Where used** | `open_paper_trades_from_s40` when at `S55_MAX_OPEN_TRADES` |
| **Execution order** | Paper open: after `should_open_trade` (with skip max-open); can still fail as `MAX_OPEN` |

---

### 6. G3.1 Production Candidate Thresholds

| Field | Value |
|-------|-------|
| **Name** | G3.1 Production Candidate Gates |
| **Symbols** | `_make_candidate` / `evaluate_symbol_candidate_g31` threshold checks |
| **Python file** | `bot/research/market_events/signal_intelligence/candidate_g31.py` (+ thresholds in `…/config.py`) |
| **Purpose** | Production filters for market-events candidates before learning / paper ingest. |
| **Input** | Symbol snapshot / score bundle (confidence, market_score, liquidity, RR, volume, funding, BTC alignment) |
| **Output** | Candidate row or reject reasons; feeds G32 outcomes / S40 |
| **Parameters** | `G3_MIN_CONFIDENCE=7.5` (`ME_G3_MIN_CONFIDENCE`); `G3_MIN_MARKET_SCORE=65`; `G3_MIN_LIQUIDITY_PROB=0.70`; `G3_MIN_RISK_REWARD=2.5`; volume ≥40; funding required; not BTC-against-trend |
| **Where used** | G3 candidate worker / pipeline; referenced by G4 validation & G39/G40 comparisons |
| **Execution order** | Before S40 ingest on market-events path |

---

### 7. G39 Experimental Profile

| Field | Value |
|-------|-------|
| **Name** | G39 Experimental Threshold Profile |
| **Symbols** | `passes_profile_g39`, `PRODUCTION_PROFILE`, `EXPERIMENTAL_PROFILE` |
| **Python file** | `bot/research/market_events/signal_intelligence/experimental_g39.py` |
| **Purpose** | Relaxed non-production profile for experimental candidate logging. |
| **Input** | Candidate metrics |
| **Output** | Pass/fail vs production & experimental profiles; experimental-only rows |
| **Parameters** | Experimental: conf≥**6.0**, score≥**45**, liq≥**0.50**, rr≥**1.8**, volume≥40; flag `ME_G3_EXPERIMENTAL_MODE` default false |
| **Where used** | Experimental G39 path / reports |
| **Execution order** | Parallel to G3.1; not production default |

---

### 8. G40 Shadow Profile

| Field | Value |
|-------|-------|
| **Name** | G40 Shadow Threshold Profile |
| **Symbols** | `passes_shadow_g40`, `SHADOW_PROFILE` |
| **Python file** | `bot/research/market_events/signal_intelligence/shadow_g40.py` |
| **Purpose** | Even more relaxed shadow opens for comparison vs production. |
| **Input** | Candidate metrics |
| **Output** | Shadow opens / traces / reports |
| **Parameters** | conf≥**5.0**, score≥**25**, liq≥**0.20**, rr≥**1.3**, volume≥**15**; `MAX_PER_DAY=20`, `MAX_PER_CYCLE=5`; `ME_SHADOW_ENABLED` default **false** |
| **Where used** | CLI `shadow-report`, `shadow-open`, `shadow-trace`, `shadow-self-test` |
| **Execution order** | Parallel shadow lane; does not replace G3.1 |

---

### 9. G4 Auto-Validation + G4.2 Threshold Optimizer

| Field | Value |
|-------|-------|
| **Name** | G4 Validation / Threshold Optimizer |
| **Symbols** | `_blocking_filter`, `run_validation_cycle_g4`, `run_threshold_optimizer_g42` |
| **Python files** | `…/auto_validation_g4.py`, `…/threshold_optimizer_g42.py` |
| **Purpose** | Record which production filter blocked a candidate; learn per-symbol thresholds (advisory). |
| **Input** | Candidates + outcomes |
| **Output** | Validation reports; table `market_validation_optimizer_g4`; false-reject / false-accept stats |
| **Parameters** | Blocking labels: Confidence, Market Score, Liquidity, RR, Funding, Volume, Trend, BTC Alignment; `FALSE_REJECT_MIN_PNL=1.0`; `FALSE_ACCEPT_MAX_PNL=-0.5`; G4.2 grids Conf `(7.8…6.5)`, Score `(72…55)`, Liq `(0.75…0.60)`, RR `(3.0…2.0)`; `ME_G4_AUTO_VALIDATION` default true |
| **Where used** | CLI `validation-report` / optimizer-report paths |
| **Execution order** | After candidate evaluate; **advisory** — does not auto-change live G3.1 |

---

### 10. Bidirectional V1.2 Candidate Filters

| Field | Value |
|-------|-------|
| **Name** | Bidirectional V1.2 Filters |
| **Symbols** | `passes_v12_filters`, `V12_CANDIDATE_SPEC`, `SHADOW_V12_ENTRY_CONFIG` |
| **Python file** | `bot/strategy/bidirectional_v12_config.py` |
| **Purpose** | Post-direction filters from counterfactual research (SFS, regime, BTC move, YES price buckets). |
| **Input** | `side`, `entry_price`, `btc_move_30s`, `seconds_from_start`, `regime`; optional `CandidateSpec` |
| **Output** | `bool` (reject → `v12_filter_reject` in observe path) |
| **Parameters** | Default `combo_robust_core`: `sfs_min=15`, `sfs_max=180`, `yes_exclude_035_040=True`, `yes_min_move=10.0`, `no_min_move=5.0`, `allowed_regimes=("NORMAL","MOMENTUM")`; base `skip_regimes=("CHOP","REVERSAL")`; EntryConfig conf≥0.55, asks≤0.45, NO avoid 0.28–0.35, max_spread=0.06 |
| **Where used** | `bot/strategy/bidirectional_observe_v12.py`; `bot/research/bidirectional_v12_counterfactual.py` |
| **Execution order** | After `evaluate_direction`; before V1.2 shadow open |

---

### 11. Bidirectional V1.2 Promotion Gates (research)

| Field | Value |
|-------|-------|
| **Name** | V1.2 Promotion Gates |
| **Symbols** | `passes_v12_gates`, `V12_GATES` |
| **Python file** | `bot/research/bidirectional_v12_counterfactual.py` |
| **Purpose** | Robustness gates for promoting a filter combo (not live entry). |
| **Input** | Counterfactual combo metrics |
| **Output** | Promote / reject combo |
| **Parameters** | `min_n=150`, `pf_min=1.50`, side PF ≥1.20, stress_b ≥1.10, fixed_2pct ≥1.20, max_cl ≤10, rolling 4/5, concentration ≤60%, bootstrap ≥0.85 |
| **Where used** | V1.2 counterfactual research CLI |
| **Execution order** | Offline after filter grid evaluation |

---

### 12. Bidirectional V1.3 Side Filters (research)

| Field | Value |
|-------|-------|
| **Name** | V1.3 Side-Specific Filters |
| **Symbols** | `SideFilter`, `apply_side_filter`, `ALL_SIDE_FILTERS`, `YES_FILTERS`, `NO_FILTERS` |
| **Python file** | `bot/research/bidirectional_v13_execution/side_filters.py` |
| **Purpose** | Test YES/NO slices independently (BTC move / entry price buckets) without joint optimization. |
| **Input** | `list[LiveTrade]`, `SideFilter` |
| **Output** | Filtered trade list for ranking |
| **Parameters** | **YES:** `yes_baseline_move5` (≥+5), `yes_move10/15`, `yes_move15_30`, `yes_excl_035_040`, buckets `025–030`, `030–035`, `040–045`. **NO:** `no_baseline_move5` (abs≥5), `no_move10/15`, `no_move15_30`, `no_entry_lt_025`, `025–030`, `035–040`, `no_excl_040_045` |
| **Where used** | `bot/research/bidirectional_v13_execution/engine.py`; promotion via `passes_promotion_gates` in `metrics.py` (`PROMOTION_GATES`: markets≥150, PF≥1.30, OOS≥1.20, …) |
| **Execution order** | Offline V1.3 ranking loop |

---

### 13. Strategy Simulator MarketFilter

| Field | Value |
|-------|-------|
| **Name** | Market Quality Filter |
| **Symbols** | `MarketFilter`, `market_passes_filter`, `list_filtered_market_paths` |
| **Python file** | `bot/research/strategy_simulator/market_filter.py` |
| **Purpose** | Chronological / quality subset of 5m markets for sim, discovery, walk-forward. |
| **Input** | Observation paths from research DB; `MarketFilter` fields |
| **Output** | `dict[slug, path]` of markets that pass |
| **Parameters / CLI** | `--market-start-ts`, `--market-end-ts`, `--min-obs-per-market` (fallback `MIN_OBS_PER_MARKET=20`), `--max-median-gap`, `--min-coverage-span` (completed span default 240s), `--completed-only` |
| **Where used** | `engine.py`, `discovery_v2.py`, `walk_forward.py`, `forward_tracker.py`, `split_diagnostics.py`; CLI `python -m bot.research.strategy_simulator` |
| **Execution order** | First step of strategy-simulator market selection; related `gate_on_validation` (`MIN_VAL_TRADES_V2=10`, `min_val_ev=0.0`) |

---

### 14. S59 Feature Lab (ON/OFF predicates)

| Field | Value |
|-------|-------|
| **Name** | Feature Lab Filters |
| **Symbols** | `_build_filters`, `evaluate_feature`, `run_feature_lab` |
| **Python file** | `bot/research/market_events/signal_intelligence/feature_lab_s59.py` |
| **Purpose** | Compare Filter ON vs OFF metrics per feature; observe-only. |
| **Input** | Closed trades via `load_lab_trades` (S56±S58, fallback S55) |
| **Output** | Tables `market_events_feature_lab_s59` / runs; CLI report |
| **Parameters** | `S59_MIN_SIDE=15`; `S59_LLM_ENABLED=False`. Predicates (ON=True): Funding LONG&lt;0 / SHORT&gt;0; FG LONG≥50 / SHORT≤50; EMA20&gt;EMA50 (or trend); ATR≥median; RSI LONG≤45 / SHORT≥55; Volume≥median; news/macro/ai ≥0.5; regime aligned (+RANGE ok); hour UTC [7,20]; weekday Mon–Thu |
| **Where used** | CLI `feature-lab` |
| **Execution order** | Offline after lab trades loaded; before / alongside S61–S65 |

---

### 15. S61 Strategy Discovery Predicates

| Field | Value |
|-------|-------|
| **Name** | Strategy Discovery Filter Hypotheses |
| **Symbols** | `_atomic_predicates`, `generate_hypotheses`, `evaluate_hypothesis`, `run_strategy_discovery` |
| **Python file** | `bot/research/market_events/signal_intelligence/strategy_discovery_s61.py` |
| **Purpose** | Compose ALLOW/BLOCK filter hypotheses; WAITING_APPROVAL only — no auto-apply. |
| **Input** | Lab trades / feature rows |
| **Output** | Hypothesis evaluations (awaiting approval) |
| **Parameters** | Atoms: funding_lt/gt/favor; fear&lt;30/&lt;40/&gt;70; ema200_up/down; hours 11–15, 7–20, 0–6; weekday Mon–Thu; AI≥0.6 / &lt;0.4; regime=*; symbol=*. `S61_MIN_N=20`; `S61_MIN_DELTA_EXPECTANCY=0.0`; env `S61_*` |
| **Where used** | CLI `strategy-discovery` |
| **Execution order** | Offline research after lab load |

---

### 16. S62 Alpha Discovery Patterns

| Field | Value |
|-------|-------|
| **Name** | Alpha Discovery Pattern Filters |
| **Symbols** | `_atoms`, `generate_patterns`, `apply_pattern_book`, `evaluate_pattern` |
| **Python file** | `bot/research/market_events/signal_intelligence/alpha_discovery_s62.py` |
| **Purpose** | Pattern book with stability + walk-forward gates. |
| **Input** | Lab trades |
| **Output** | Ranked patterns that pass stability / walk-forward |
| **Parameters** | Atoms: funding; FearGreed&lt;25/&lt;30/&gt;70; BTC↔EMA200; RSI&lt;30/&gt;70; hours 14–18, 11–15, Asian; dir LONG/SHORT; regime; coin. `S62_MIN_N=50` (env) |
| **Where used** | CLI `alpha-discovery` |
| **Execution order** | Offline research after lab load |

---

### 17. S65 Filter Simulator

| Field | Value |
|-------|-------|
| **Name** | Strategy Filter Simulator |
| **Symbols** | `simulate_remove`, `run_filter_simulations`, `run_filter_simulator` |
| **Python file** | `bot/research/market_events/signal_intelligence/filter_simulator_s65.py` |
| **Purpose** | Counterfactual: remove S64 PnL-killer segments / 2D combos; rank ΔPnL. No auto-apply. |
| **Input** | `load_lab_trades` + S64 killers (`pnl_killers_s64`) |
| **Output** | `research/reports/pnl/filter_simulator.md` + `.json` |
| **Parameters** | `TOP_FILTERS=20`; `COMBO_DIMS=("direction","regime","hour","strategy","coin")`; killers top from S64 `TOP_N=20` |
| **Where used** | CLI `simulate-filters` |
| **Execution order** | Offline **after** S64 pnl-killers |

---

### 18. Optimizer BTC Filter Grid

| Field | Value |
|-------|-------|
| **Name** | Optimizer BTC Filter Grid |
| **Symbols** | `BTC_FILTER_GRID`, `StrategyParams.btc_filter_usd`, `simulate_trade` |
| **Python files** | `bot/optimizer/constants.py`, `bot/optimizer/replay.py`, `bot/optimizer/grid.py` |
| **Purpose** | Offline grid: drop ER v2 trades where `btc_move_30s > btc_filter_usd` (and `entry_price > max_entry`). |
| **Input** | `early_reversion_v2_trades` |
| **Output** | Grid search best params / recommendations |
| **Parameters** | Grid `(5, 10, 15, 20, 25, 30)` USD; baseline `btc_filter_usd=999.0` (effectively off) |
| **Where used** | Optimizer grid / report render |
| **Execution order** | Offline replay over closed ER trades |

---

### 19. Evolution Regime Shadow Filter

| Field | Value |
|-------|-------|
| **Name** | Evolution Regime Shadow |
| **Symbols** | `DEFAULT_FILTER_REGIMES`, `create_regime_shadow`, `sync_regime_shadow`, `_finalize_regime_shadow` |
| **Python file** | `bot/evolution/regime_shadow.py` |
| **Purpose** | Counterfactual: skip NO_C entries in named regimes; score saved losses vs missed profits. |
| **Input** | Trade / regime stream; shadow counters |
| **Output** | Verdict `PROMOTE_FILTER` or `REJECT_FILTER`; report section |
| **Parameters** | Default regimes `("Strong Uptrend", "News Spike")`; `SHADOW_TARGET_SAMPLE` (from `bot/evolution/constants`); promote if PF improvement ≥ **10%** |
| **Where used** | `bot/daily/pipeline.py`; report render `render_regime_shadow_section` |
| **Execution order** | Daily / evolution shadow lane — **does not block live** until promoted manually |

---

## Related gates (not primary “filters” but pipeline-adjacent)

| Name | File | Role |
|------|------|------|
| Futures signal gate `passes_signal_gate` | `bot/research/futures/parser_v2.py` | Taxonomy/structure gate for Telegram signals |
| Futures outcome gate `run_outcome_gate` | `bot/research/futures_agent/signal_outcome_gate.py` | Research completeness (not entry) |
| Strategy review `check_safety_gate` | `bot/strategy_review/safety.py` | Blocks unsafe param recommendations |
| Report BTC filter analysis | `bot/report/analytics.py` (`build_btc_filter_analysis`) | Offline buckets / recommended NO_C thresholds |
| Cost model filter | `bot/research/strategy_simulator/cost_model.py` | Simulator cost filtering |

---

## Filter reports on disk

| Report | Producer | Notes |
|--------|----------|-------|
| `research/reports/pnl/filter_simulator.md` | S65 `run_filter_simulator` | Present locally (1786 hist lab trades in last run) |
| `research/reports/pnl/filter_simulator.json` | S65 | Machine-readable twin |
| `research/reports/pnl/pnl_killers.md` / `.json` | S64 | Input killers for S65 |
| Live / shadow counters in SQLite | NO_C filters | `no_c_filter_live_counters`, `no_c_filter_shadow_*` in `trades.db` |
| Feature lab table | S59 | `market_events_feature_lab_s59` (may be empty locally) |
| Regime ops | S57 | `market_events_regime_ops_s57` |
| Validation optimizer | G4.2 | `market_validation_optimizer_g4` |

Filesystem reports under `research/reports/` are often gitignored; SQLite counters live in `data/trades.db` / market-events DBs (see `PROJECT_OS/DATABASE_INVENTORY.md`).

---

## Quick symbol → file index

| Symbol | File |
|--------|------|
| `resolve_no_c_filter` | `bot/no_c_btc_filter.py` |
| `record_no_c_filter_shadow` | `bot/no_c_filter_shadow.py` |
| `apply_regime_gate` | `…/market_regime_s57.py` |
| `should_open_trade` | `…/trade_intelligence_s55.py` |
| `maybe_replace_weakest_for_candidate` | `…/portfolio_manager_s55.py` |
| G3.1 thresholds | `…/candidate_g31.py` + `…/config.py` |
| `passes_profile_g39` | `…/experimental_g39.py` |
| `passes_shadow_g40` | `…/shadow_g40.py` |
| `_blocking_filter` / G4.2 | `…/auto_validation_g4.py`, `…/threshold_optimizer_g42.py` |
| `passes_v12_filters` | `bot/strategy/bidirectional_v12_config.py` |
| `apply_side_filter` | `…/bidirectional_v13_execution/side_filters.py` |
| `market_passes_filter` | `bot/research/strategy_simulator/market_filter.py` |
| `_build_filters` (S59) | `…/feature_lab_s59.py` |
| S61 / S62 predicates | `…/strategy_discovery_s61.py`, `…/alpha_discovery_s62.py` |
| `run_filter_simulator` | `…/filter_simulator_s65.py` |
| `BTC_FILTER_GRID` | `bot/optimizer/constants.py` |
| `DEFAULT_FILTER_REGIMES` | `bot/evolution/regime_shadow.py` |

---

## Related PROJECT_OS docs

- `DATABASE_INVENTORY.md` — filter-related tables / row counts
- `ARCHITECTURE.md` — leakage policy (do not feed exit/MFE into entry filters)
- `FUTURES_RESEARCH.md` — re-run filter sims only on `universe=futures_paper`
- `NEXT_TASK.md` — locate ~28k futures paper before more filter work
