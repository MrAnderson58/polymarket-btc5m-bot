# PROJECT_CONTEXT — Polymarket BTC 5m Trading Bot

## Architecture Overview

Paper/live trading bot for Polymarket BTC 5-minute binary options. Early reversion strategy family with AI analytics layer.

### Hard Constraints (NEVER modify)

- `bot/execution.py` — CLOB order execution
- `bot/main.py` — main trading loop
- `bot/early_reversion.py`, `bot/early_reversion_v2.py` — strategy logic
- `bot/strategy.py` — strategy definitions
- Entry/exit logic, stop loss, trailing stop behavior

### Core Strategy Parameters (current)

| Parameter | Value | Source |
|-----------|-------|--------|
| Entry threshold | 0.40 (effective) | `config.py` |
| Stop loss | -10% | `ER_V2_STOP_LOSS_PCT` |
| Trailing activation | 3% | `TRAILING_ACTIVATION_PROFIT` |
| Trailing distance | 1% | `TRAILING_OFFSET` |
| Time stop | 90s | `ER_V2_TIME_STOP_SEC` |

---

## Intelligence Stack (observe-only, no execution impact)

### 1. Optimizer (`bot/optimizer/`)
- Grid search over entry/stop/trailing parameters
- Walk-forward validation (2 folds, trend: stable)
- Overfit detection (currently: MEDIUM)
- Cache: `optimizer_cache/optimizer_results.json`
- Optimal found: entry=0.34, stop=-10, trail_act=0.045, trail_dist=0.005

### 2. Scientist (`bot/scientist/`)
- Hypothesis generation from patterns
- Experiment tracking (PASSED/FAILED/REJECTED)
- Quality gates (sample ≥300, walk-forward, low overfit)
- Current state: all experiments REJECTED/FAILED (need more data)

### 3. Trading Brain (`bot/trading_brain/`)
- Causal knowledge extraction from trade features
- Top knowledge: entry_price=0.35 → positive, entry_price=0.34 → negative
- Market regime: Mean Reversion → positive, Panic → negative
- 5 causal rules stored

### 4. AI Agent (`bot/ai_agent/`)
- Per-trade ALLOW/SKIP/SHADOW decisions (counterfactual)
- Current: ALLOW=246, SKIP=3, SHADOW=264 (513 total)
- Strongly supports changes (ALLOW >> SKIP)

### 5. Strategy Review (`bot/strategy_review/`)
- Entry/stop/trailing analysis with safety gate
- Current verdict: KEEP CURRENT SETTINGS
- Reason: 0 trades since last change (needs 300)
- Safety gate: BLOCKED

### 6. Evolution (`bot/evolution/`)
- Decision engine: KEEP / WATCH / READY_FOR_SHADOW / SHADOW_*
- Auto Review: multi-parameter consensus (Optimizer + Scientist + Brain + AI + Review)
- Shadow layer: counterfactual WOULD_ENTER / WOULD_SKIP tracking
- **Active shadow experiment**: entry_threshold 0.40 → 0.39 (ID=1, target=200 trades)

### 7. Strategy Surgeon (`bot/evolution/surgeon.py`)
- Analyzes last 500 trades only
- 6 diagnostic questions → 1 recommendation
- Current recommendation: "Lower max entry to 0.36" (PF 3.05 at 0.36 vs 1.65 baseline)

### 8. Trading Intelligence (`bot/analytics/intelligence*.py`)
- Entry price analysis, drift detection
- Cache: `intelligence_cache/`

### 9. Portfolio (`bot/portfolio/`)
- Position sizing, kill switch, daily loss limits
- Live readiness checks
- Tables: `portfolio_state`, `live_journal`

---

## Current Performance (last 500 trades)

| Metric | Value |
|--------|-------|
| PF | 1.646 |
| Win Rate | 52.8% |
| Total trades | 513 |

### Entry Price Breakdown

| Entry | Trades | WR | PF | Net |
|-------|--------|-----|------|------|
| 0.34 | 29 | 58.6% | 1.05 | +17.9% |
| 0.35 | 71 | 62.0% | 3.05 | +1245.7% |
| 0.36 | 36 | 52.8% | 3.05 | +586.1% |
| 0.37 | 36 | 52.8% | 1.27 | +94.6% |
| 0.38 | 58 | 51.7% | 2.31 | +692.1% |
| 0.39 | 55 | 50.9% | 1.57 | +402.6% |
| **0.40** | **111** | **42.3%** | **0.91** | **-144.8%** |

**Key finding**: Entry at 0.40 is the only bucket with PF < 1 and negative net. Removing it improves overall PF from 1.646 → 1.919 (+16.6%). t-stat=2.76, df=203.

---

## Active Shadow Experiment

| Field | Value |
|-------|-------|
| ID | 1 |
| Parameter | entry_threshold |
| Current | 0.40 |
| Shadow | 0.39 |
| Status | RUNNING |
| Progress | 0 / 200 |
| Created | 2026-07-03 |

**Hypothesis**: Lowering max entry from 0.40 to 0.39 will increase PF by ~16% while maintaining similar DD.

**Evidence**:
- Optimizer: optimal entry=0.34, improvement +83% (aggressive)
- Surgeon: recommends ≤0.36 (conservative estimate)
- Brain: entry_price=0.35 → positive causal signal
- AI Agent: ALLOW=246 >> SKIP=3 (strongly supports change)
- Walk-forward: stable, generalizes across 2 folds
- Shadow uses conservative step (0.39) rather than optimizer's aggressive 0.34

**Verdict at 200 trades**: PROMOTE if shadow PF ≥ live PF × 1.10 AND shadow DD ≤ live DD. Otherwise REJECT.

---

## Daily Pipeline (`python -m bot.daily`)

```
Feature sync → Optimizer → Scientist → Brain → AI Agent →
Intelligence → Strategy Review → Report → Evolution → Surgeon
```

Output blocks at end:
1. EVOLUTION STATUS (KEEP / WATCH / SHADOW RUNNING / SHADOW COMPLETE)
2. STRATEGY SURGEON (6 questions + 1 recommendation)

---

## Report Sections (`python -m bot.report`)

§1–§44: Standard analytics
§45: Strategy Review
§46: Evolution
§47: Shadow Evolution
§48: Strategy Surgeon
Appendix: Optimizer, Features, Monte Carlo, Performance

---

## Known Issues / Technical Debt (25 findings)

### Dead code (8 items)
- `bot/ai_agent/models.py` — entire ML predictor hierarchy (`RandomForestPredictor`, `XGBoostPredictor`, `LightGBMPredictor`, `get_predictor()`) — ~200 lines, never called
- `bot/ai_agent/features.py:feature_vector()` — only used by dead predictor classes
- `bot/ai_agent/learning.py:load_ai_features_from_decisions()` — trivial wrapper, never imported
- `bot/report/cache_loader.py:load_ai_sections()` — never called, builder uses individual loaders
- `bot/optimizer/dataset.py:_btc_volatility()`, `_quote_at_entry()` — superseded by MarketDataCache
- `bot/perf/feature_store.py:enriched_by_trade_id()` — never imported
- `bot/evolution/decision.py:_parse_change_text()` — no longer called after auto_review refactor
- Legacy top-level modules: `bot/analytics.py`, `bot/analytics_early_reversion*.py`, `bot/performance.py`, `bot/daily_report.py`, `bot/paper_trader.py`, `bot/scoring/`, `bot/recommendations/`

### Duplicated logic (9 items, priority order)
1. **`_metrics()` x4** — `report/analytics`, `ai_agent/research`, `trading_brain/knowledge`, `evolution/metrics` — same PF/WR formula
2. **SimilarityEngine x2** — `ai_agent/similarity.SimilarTradesEngine` vs `trading_brain/similarity.SimilarityEngineV2` — near-identical kNN
3. **Overfit/stability/walk-forward x2** — `strategy_review/metrics` vs `scientist/validation` — same checks, minor threshold diffs
4. **`trade_pnl()` x3** — `report/analytics`, `evolution/metrics`, `evolution/surgeon`
5. **`_pf()` standalone** — `surgeon.py` redefines what `lane_metrics()` already does
6. **Permutation p-value x2** — `report/advanced` (2000 iter) vs `strategy_review/metrics` (1500 iter)
7. **`confidence_label()` x2** — `report/advanced` vs `optimizer/recommendations`
8. **`SOURCE_TABLE` x5** — defined in 5 separate modules instead of one import
9. **`_aggregate_neighbors()` x2** — identical in both similarity engines

### Repeated computations (6 items)
1. `build_daily_report(conn)` called **twice** per report (cache_loader + evolution builder)
2. `load_enriched_trades(conn)` called **3 times** in scientist cycle (not passed through)
3. `p_value_vs_rest()` + `overfit_risk()` computed **twice per alternative** in stop_loss analysis
4. Same double-computation in trailing analysis
5. `fetch_bid_series()` re-queried for overlapping combos in `combinations.py`
6. `SOURCE_TABLE` string constant defined 5 times (maintenance risk)

### Heavy import chains
- `bot/report/analytics.py` → `er_btc_direction_stats` → `er_stats` → `early_reversion` → `execution` → `py_clob_client_v2`
- Prevents unit testing without CLOB SDK
- Evolution modules isolate with `bot/evolution/metrics.py` (lightweight reimplementation)

---

## Git

- Branch: `migration/clob-v2`
- Reports `.gitignore`d (generated locally)
- Database `.gitignore`d

---

## Verification Commands

```bash
python -m bot.daily --quick       # Full pipeline (quick optimizer)
python -m bot.report              # Read-only report assembly
python -m bot.report --profile    # With profiling
python -m unittest tests.test_evolution -v  # Evolution tests (13 pass)
```

---

_Last updated: 2026-07-03_
