# Research Dataset Quality Audit — Phase 5A

**Date:** 2026-07-25  
**Scope:** Features available through `load_lab_trades()` (S59–S66 research universe)  
**Constraint:** Documentation + research report artifacts only. **No application / trading / paper / strategy code changes.**

**Inputs:** `docs/ARCHITECTURE_AUDIT.md`, `docs/TRADE_DATA_FLOW.md`, `docs/CONSOLIDATION_PLAN.md`

**Artifacts (local; under `research/reports/` which is gitignored):**

| Path | Contents |
|------|----------|
| `research/reports/features/feature_quality.md` | Human-readable quality + bucket tables |
| `research/reports/features/feature_quality.json` | Full per-feature stats, rankings, detections |
| `research/reports/features/feature_rankings.csv` | Ranked feature importance table |

---

## 1. What was audited

### Loader (unchanged)

```sql
SELECT s.*, d.ema20 AS s58_ema20, d.ema50 AS s58_ema50, d.ema200 AS s58_ema200,
       d.rsi AS s58_rsi, d.btc_return AS s58_btc_return
FROM market_events_trade_snapshots_s56 s
LEFT JOIN market_events_trade_decisions_s58 d
  ON d.paper_trade_id = s.paper_trade_id
WHERE s.pnl_usd IS NOT NULL
```

Implementation: `feature_lab_s59.load_lab_trades` (merges S58 EMA/RSI/btc_return into `ema*` / `rsi` / `btc_return` when present).

### Feature surface analyzed

1. **Top-level S56 columns** returned by the loader (excluding opaque `snapshot_json` blob as a single field).  
2. **Flattened `snapshot_json` / `snapshot_json.features.*`** keys present on rows.  
3. **Derived open-time helpers** (research-only, for audit): `derived.coin`, `derived.session`, `derived.is_hist`, `derived.ai_score_0_100`, optional EMA distance / confidence / dominance when source fields exist.

**Labels:** `pnl_usd` (regression / expectancy), `win = pnl_usd > 0` (classification).

### Local universe measured

| Metric | Value |
|--------|-------|
| `n_trades` | **1786** |
| Win rate | **59.91%** |
| Profit factor | **1.297** |
| Expectancy | **+0.0364** USD / trade |
| Net PnL | **+64.92** USD |
| `s40_signal_type` | **100% `hist:*`** (no live paper closes in this RESEARCH DB) |

| `s40_signal_type` | Trades | Win% | PF | Exp | Net PnL |
|-------------------|-------:|-----:|---:|----:|--------:|
| `hist:er_v2` | 513 | 51.85 | 1.59 | +0.070 | +35.81 |
| `hist:er_v1` | 493 | 80.32 | 0.91 | −0.016 | −7.91 |
| `hist:er_v3` | 282 | 39.01 | 1.50 | +0.048 | +13.40 |
| `hist:er_v25` | 221 | 50.68 | 1.66 | +0.055 | +12.18 |
| `hist:yes_c_shadow` | 124 | 45.97 | 1.24 | +0.033 | +4.06 |
| `hist:v4_shadow` | 107 | 77.57 | 2.00 | +0.037 | +3.99 |
| `hist:virtual` | 46 | 100.0 | ∞ | +0.073 | +3.38 |

**Critical context (from Phase 2):** this lab universe is **history-backfilled RESEARCH snapshots**, not the LIVE S42 paper book. Classical S55 open-time market features are largely absent.

---

## 2. Headline findings

1. **Most S59-style market features are dead (0% fill)** on this DB: `funding`, `atr`, `fear_greed`, `oi_delta`, `volume`, `trend`, `vwap`, `market_regime`, `macro_score`, `news_score`, `market_score`, `etf_flow`, `tp1`, `tp2`, and S58 join fields (`ema20/50/200`, `rsi`, `btc_return`).  
2. **Only ~513/1786 rows (`hist:er_v2`)** carry a rich nested ER feature pack (`features.btc_move_*`, `features.spread`, volatilities, MFE/MAE, …).  
3. **Strongest “predictors” are often leakage or provenance proxies** (exit reason, is_trailing, market_slug, source_table, strategy family) — not tradeable open-time edge.  
4. **Among safer open-time fields**, weak but non-zero association appears for `hour`, `weekday`, `direction`, `spread` / short-horizon volatility (ER-v2 subset), and sparse `ai_score` (~6% fill).  
5. **Duplicate / near-duplicate columns** are common (`spread` ≡ `features.spread`, `volatility` ≡ `features.volatility_60s`, coin ≡ symbol, timestamps mirrored in JSON).

---

## 3. Fill rates — classical vs hist ER pack

### Dead / empty classical features (fill ≈ 0%)

`funding`, `atr`, `fear_greed`, `oi_delta`, `volume`, `trend`, `vwap`, `market_regime`, `macro_score`, `news_score`, `market_score`, `etf_flow`, `expected_pnl_pct`, `tp1`, `tp2`, plus matching `json.*` nulls, and S58 EMA/RSI/btc_return (join empty).

→ S59 filters that depend on these fields **skip almost all rows** on this universe (predicates return `None`).

### Partially filled

| Feature | Fill% | Notes |
|---------|------:|-------|
| `ai_score` | 6.0 | Sparse; mostly non-ER hist rows |
| `volatility` / `features.volatility_60s` | 28.7 | ER-v2 only |
| `spread` / `features.spread` | 28.6 | ER-v2 only |
| `features.btc_move_*`, `features.mfe/mae`, asks/bids | ~28.7 | ER-v2 nested pack |
| `exit_reason` | 69.8 | Outcome label |
| `exit_price` / `pnl_pct` | 97.4 | Outcome |

### Fully filled identity / clock / side

`direction`, `symbol`, `hour`, `weekday`, `s40_signal_type`, `entry`, `timestamp`/`created_at`, `pnl_usd`, `duration_sec`, `trailing`, `derived.session`, `derived.coin`, `derived.is_hist`.

---

## 4. Categorical performance (selected)

### `direction`

| Value | Trades | Win% | PF | Exp | Net PnL |
|-------|-------:|-----:|---:|----:|--------:|
| SHORT | 929 | 60.39 | 1.44 | +0.051 | +47.13 |
| LONG | 857 | 59.39 | 1.16 | +0.021 | +17.79 |

### `s40_signal_type`

See universe table in §1 — **strategy/family provenance dominates PnL mix**; treating it as a “market feature” overstates edge.

### Session (`derived.session` from hour)

Computed from UTC hour via shared `session_from_hour` helper (Asia / London / NewYork / Offhours). Full value table is in `feature_quality.json` / `.md`.

---

## 5. Continuous distributions & buckets (filled)

### `hour` (n=1786)

| | |
|--|--|
| min / max | 0 / 23 |
| median / mean | 13 / 12.27 |
| p5 / p25 / p75 / p95 | 1 / 6 / 19 / 22 |
| outliers (IQR rule) | 0 |

Bucket sketch: hours **02–05** show higher expectancy (~+0.09); **05–10** negative in this sample. Details in report files.

### `volatility` (n=513, ER-v2)

| | |
|--|--|
| min / max | 0.0035 / 56.27 |
| median / mean | 7.53 / 9.62 |
| p5 / p95 | 1.29 / 25.40 |
| outlier_rate | ~5.7% |

Lower-volatility quantile buckets show higher PF in-sample; mid buckets weaken — **subset-only**, not a live feature.

### Requested continuous families

| Family | Status on this DB |
|--------|-------------------|
| AI score | Sparse (6%); 0–100 normalize available as `derived.ai_score_0_100` |
| Funding | **Dead** |
| OI | **Dead** (`oi_delta`) |
| ATR | **Dead** |
| Confidence | **Dead** (no decision_confidence in hist pack) |
| BTC dominance | **Dead** |
| Fear & Greed | **Dead** |
| Volume / volume ratio | **Dead** |
| Distance from EMA | **Unavailable** (no ema20/50) |
| BTC return | **Dead** (S58 empty) |
| Spread / short vol | Partial (ER-v2) |

Automatic quantile / score buckets for every non-dead numeric feature are in `feature_quality.json` → `features[].buckets`.

---

## 6. Information measures & importance

For each feature (where fill ≥ ~1% and n≥30):

- Pearson **corr → pnl_usd**
- Pearson **corr → win**
- Sklearn **mutual_info_regression** (pnl)
- Sklearn **mutual_info_classif** (win)
- Discretized **information gain ≈ MI(win, binned x)**
- Bucket-mid vs bucket-expectancy correlation when buckets exist
- Composite **importance_score** (mean of available |corr|/MI/IG terms; leakage-penalized)

Full ranking: `feature_rankings.csv`.

### Top 20 predictive (open-time / non-leakage preferred)

| # | Feature | Fill% | corr(pnl) | MI(pnl) | IG(win) | Score |
|--:|---------|------:|----------:|--------:|--------:|------:|
| 1 | `features.market_slug` | 28.7 | −0.022 | 0.093 | 0.651 | 0.372 |
| 2 | `entry` | 100 | +0.020 | 1.268 | 0.003 | 0.342 |
| 3 | `json.market_slug` | 100 | −0.003 | 0.140 | 0.407 | 0.319 |
| 4 | `json.source_table` | 100 | +0.045 | 0.636 | 0.066 | 0.271 |
| 5 | `s40_signal_type` | 100 | +0.033 | 0.637 | 0.066 | 0.268 |
| 6 | `json.strategy` | 100 | −0.023 | 0.370 | 0.023 | 0.146 |
| 7 | `features.ask` | 28.6 | −0.012 | 0.223 | 0.007 | 0.083 |
| 8 | `features.bid` | 28.6 | −0.053 | 0.158 | 0.007 | 0.075 |
| 9 | `features.distance_to_strike` | 28.6 | −0.142 | 0.031 | 0.011 | 0.065 |
| 10 | `hour` | 100 | +0.042 | 0.100 | 0.006 | 0.051 |
| 11 | `weekday` | 100 | +0.044 | 0.078 | 0.008 | 0.046 |
| 12 | `features.spread` | 28.6 | +0.118 | 0.011 | 0.003 | 0.045 |
| 13 | `spread` | 28.6 | +0.118 | 0.011 | 0.003 | 0.045 |
| 14 | `features.volatility_15s` | 28.2 | −0.112 | 0.000 | 0.007 | 0.042 |
| 15 | `features.volatility_30s` | 28.6 | −0.087 | 0.000 | 0.014 | 0.038 |
| 16 | `ai_score` | 6.0 | +0.075 | 0.000 | 0.017 | 0.036 |
| 17 | `derived.ai_score_0_100` | 6.0 | +0.075 | 0.000 | 0.017 | 0.036 |
| 18 | `direction` | 100 | +0.038 | 0.060 | ~0 | 0.033 |
| 19 | `features.volatility_60s` | 28.7 | −0.046 | 0.032 | 0.009 | 0.032 |
| 20 | `volatility` | 28.7 | −0.046 | 0.032 | 0.009 | 0.032 |

**Interpretation:** ranks 1–6 are mostly **instrument / provenance / price-level proxies**, not independent microstructure alphas. Prefer ranks emphasizing `hour`, `weekday`, `direction`, `spread`, short volatilities, sparse `ai_score` when designing live-safe research — and re-run once non-hist S56 rows exist.

### Top 20 useless

Dominated by **0% fill** classical fields: `atr`, `funding`, `fear_greed`, `oi_delta`, `volume`, `trend`, `vwap`, `market_regime`, scores, `tp1`/`tp2`, and empty `json.*` mirrors. See report for the full list.

### Top 20 suspicious

Highest scores with **leakage or subset artifacts**, including:

- `features.exit_reason`, `features.is_trailing` (post-open / outcome)  
- `features.pnl` / `pnl_usd` / `pnl_pct` / `exit_price` (labels)  
- `features.mfe` / `features.mae` / `features.is_win` (path / outcome)  
- `features.btc_move_*` (post-entry path)  
- `market_slug` / `entry` (may proxy market identity or BTC price epoch, not strategy edge)

---

## 7. Detections

| Class | Count / examples |
|-------|------------------|
| **Dead** (fill &lt; 1%) | **37** — classical market features listed in §3 |
| **Constant / almost constant** | See JSON `detections` (few among filled hist fields) |
| **Duplicate pairs** (identical filled values) | **7** e.g. `derived.coin`≡`symbol`, `features.spread`≡`spread`, `features.volatility_60s`≡`volatility`, `features.mfe`≡`json.mfe`, timestamp mirrors |
| **Highly correlated \|r\|≥0.95** | **32** pairs — mostly timestamp clones, entry≡entry_price, exit≡features.exit_price, adjacent `btc_move_*` horizons |
| **Unused for S59 filters** | Empty classical filter inputs (`funding`, `fear_greed`, EMA/trend, `atr`, `rsi`, `volume`, …) cannot fire ON/OFF on this universe |
| **Leakage-flagged** | **40** features (outcome, path, excursion, or heuristic post-open) |

---

## 8. Data leakage review

| Feature class | Available at open? | Risk if used as entry filter |
|---------------|--------------------|------------------------------|
| `pnl_*`, `exit_*`, `duration_sec`, `features.is_win/loss/stop` | **No** | Direct label leakage |
| `features.mfe` / `mae` | **No** (needs full path) | Severe leakage |
| `features.btc_move_5s…90s` | **No** (post-entry) | Severe leakage |
| `features.exit_reason`, `is_trailing` | **No** / outcome-tied | Leakage |
| `tp1`/`tp2`/`trailing` columns | Plan vs outcome ambiguous; here mostly empty or hist flags | Treat as suspicious |
| `hour`, `weekday`, `direction`, `symbol`, entry quotes, distance_to_strike, pre-trade spread/vol | **Yes** (when populated) | OK if truly measured pre-trade |
| `s40_signal_type` / `source_table` / `strategy` | Known at open as **provenance**, not market state | OK for segmentation; not an alpha feature |
| S55 confidence / regime / funding | Would be open-time **if present** | Missing here |

**Rule for later phases:** never feed leakage-flagged fields into filter simulators or pattern discovery as entry predicates without an explicit “post-trade forensics only” label.

---

## 9. Implications for S59–S66 (no code changed)

1. **Do not trust live-style Feature Lab conclusions on this RESEARCH file** until non-`hist:*` rows (or a paper-only universe) exist with filled funding/ATR/regime/AI.  
2. **ER-v2 nested features are a different schema** from S55/S56 live columns — mixing them without a `universe=` flag confounds reports (already warned in Phase 2/3).  
3. **Consolidation next steps** (still research-safe): provenance tags, paper vs hist universe split, and refusing leakage fields in discovery — *not* changing open/close logic.  
4. Re-run this audit after: (a) paper closes land in RESEARCH S56, or (b) history backfill maps classical open-time fields.

---

## 10. Method notes

- Ephemeral analysis run against `research_connection()` + `load_lab_trades()`; **no bot package files modified**.  
- Outliers: Tukey IQR 1.5× (fallback p1/p99 when IQR=0).  
- Continuous buckets: score-like 0–10…100 when applicable; else quantile edges.  
- Importance is descriptive for this sample — not a license to change production gates.

---

## 11. Deliverables checklist

| Deliverable | Status |
|-------------|--------|
| `docs/RESEARCH_DATASET_AUDIT.md` | **This file** |
| `research/reports/features/feature_quality.md` | Written |
| `research/reports/features/feature_quality.json` | Written |
| `research/reports/features/feature_rankings.csv` | Written |
| Trading / paper / open-close / strategy code | **Untouched** |

---

*End of Phase 5A Research Dataset Quality Audit.*
