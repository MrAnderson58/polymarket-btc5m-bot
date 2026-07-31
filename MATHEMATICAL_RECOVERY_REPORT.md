# MATHEMATICAL_RECOVERY_REPORT

_Signal Mathematics Recovery V1 — analysis only. Gate / Trading / Paper / Execution unchanged._

## 1 Current pipeline

```
Market → Collector → Candidate → Features → Gate → S55 → S42 → Close → Research → Feature Store → ML
```

- S42 closed samples: **50**
- S55 rows: **150**
- Pipeline bottlenecks: **3**

- Market / Snapshots: in=None out=2566 window=652 drop%=None
- Collector → Candidates (G31): in=652 out=51220 window=13040 drop%=None
- Candidates → S40 Signals: in=13040 out=10800 window=3960 drop%=69.63
- Signals → Features (S55): in=3960 out=150 window=100 drop%=97.47 ⚠ CRITICAL BOTTLENECK
- Gate (kept vs logged): in=150 out=5 window=None drop%=96.67 ⚠ CRITICAL BOTTLENECK
- Gate → Paper (S42): in=50 out=50 window=0 drop%=66.67
- Paper → Closed: in=50 out=50 window=0 drop%=0.0
- Closed → Research / Feature Store: in=50 out=3 window=None drop%=None
- Feature Store → ML dataset: in=50 out=50 window=None drop%=0.0

## 2 Broken features

- `ema200_distance`
- `ema20_distance`
- `ema50_distance`
- `rsi`
- `vwap_distance`

## 3 Constant features

- `atr`
- `direction`
- `ema_trend`
- `fear_greed`
- `funding`
- `hour`
- `market_regime`
- `pattern`
- `regime_confidence`
- `trend`
- `volatility`
- `volume`
- `weekday`

## 4 Empty features

- `ai_score`
- `book_imbalance`
- `btc_move`
- `confidence`
- `decision_confidence`
- `etf_flow`
- `liquidation_metric`
- `macd`
- `macro_score`
- `news_category`
- `news_score`
- `shock_score`
- `spread`
- `time_to_expiry`

## 5 Dead collectors

- `NONE (missing producer)` → features `['rsi']` (rsi=None hardcoded in build_entry_features)
- `NONE (missing producer)` → features `['ema*_distance', 'vwap_distance']` (distance derivation dead without level inputs)

## 6 Dead tables

- `market_events_signal_outcomes_f1` (F1 outcomes) rows=0
- `market_live_signals_g3` (live g3 signals) rows=0
- `ai_paper_trades_s47` (AI paper trades) rows=0

## 7 Useful features

- `symbol`
- `oi_delta`
- `gate_decision`

## 8 Noise features

- `direction`
- `pattern`
- `news_category`
- `market_regime`
- `confidence`
- `ai_score`
- `macro_score`
- `news_score`
- `volatility`
- `atr`
- `vwap_distance`
- `ema20_distance`
- `ema50_distance`
- `ema200_distance`
- `rsi`
- `macd`
- `funding`
- `liquidation_metric`
- `spread`
- `book_imbalance`
- `time_to_expiry`
- `btc_move`
- `volume`
- `fear_greed`
- `trend`
- `hour`
- `weekday`

## 9 Best feature combinations

- `oi_delta + gate_decision` — n=5 EV=2.5435 PF=1.0553 WR=60.0 p=0.5025
- `symbol + oi_delta` — n=13 EV=-19.7345 PF=0.5505 WR=30.77 p=0.8209

## 10 Recommended production feature set

- `symbol`
- `oi_delta`
- `gate_decision`
- `direction`
- `market_regime`
- `hour`
- `weekday`

## 11 Recommended ML feature set

- `symbol`
- `oi_delta`
- `gate_decision`

## 12 Recommended research feature set

- `btc_dominance`
- `gate_decision`
- `gate_expected_pnl_pct`
- `oi_delta`
- `open_interest`
- `regime_score`
- `similar_count`
- `symbol`

## 13 Dead code

- `rsi=None hardcoded in build_entry_features`
- `distance derivation dead without level inputs`

## 14 Critical bugs

- BROKEN SOURCE: RSI — Hardcoded rsi=None in build_entry_features — never computed from candles.
- BROKEN SOURCE: VWAP — VWAP not populated in S55/S40 — distances always NULL in Feature Store.
- CRITICAL: constant/empty columns: direction, pattern, news_category(all_null), market_regime, confidence(all_null), ai_score(all_null), macro_score(all_null), news_score(all_null), volatility, atr, vwap_distance(all_null), ema20_distance(all_null), ema50_distance(all_null), ema200_distance(all_null), rsi(all_null), macd(all_null), funding, liquidation_metric(all_null), spread(all_null), book_imbalance(all_null)

## 15 Immediate fixes

1. Implement real RSI from candles into build_entry_features (stop hardcoding None).
2. Persist EMA20/50/200 (or distances) from candle history into S55/Feature Store.
3. Compute and store VWAP so vwap_distance is non-null.
4. Fix atr: currently CONSTANT placeholder — wire per-symbol live values.
5. Fix funding: currently CONSTANT placeholder — wire per-symbol live values.
6. Fix fear_greed: currently CONSTANT placeholder — wire per-symbol live values.
7. Fix trend: currently CONSTANT placeholder — wire per-symbol live values.
8. Fix volume: currently CONSTANT placeholder — wire per-symbol live values.
9. Populate decision_confidence on S40/S42 — required for optimizer confidence gates.
10. Unblock pipeline: Candidates(window) → S55(window) drop 99.23% (CRITICAL BOTTLENECK).
11. Unblock pipeline: S55(all) → Gate-pass drop 96.67% (CRITICAL BOTTLENECK).
12. Unblock pipeline: S40(window) → S42(window opens) drop 100.0% (CRITICAL BOTTLENECK).
13. CRITICAL: constant/empty columns: direction, pattern, news_category(all_null), market_regime, confidence(all_null), ai_score(all_null), macro_score(all_null), news_score(all_null), volatility, atr, vwap_distance(all_null), ema20_distance(all_null), ema50_distance(all_null), ema200_distance(all_null), rsi(all_null), macd(all_null), funding, liquidation_metric(all_null), spread(all_null), book_imbalance(all_null)

## Safety

- Read-only diagnostics
- No Gate / Trading / Paper / Execution modifications
