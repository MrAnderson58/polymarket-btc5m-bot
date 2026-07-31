# Feature Audit V1 — Signal Mathematics Recovery

_Generated ts=1785515341 | closed samples=50 | S55 rows=150_

Read-only. No Gate / Trading / Paper / Execution changes.

## Status summary

- **BROKEN**: 5 — `ema200_distance, ema20_distance, ema50_distance, rsi, vwap_distance`
- **CONSTANT**: 13 — `atr, direction, ema_trend, fear_greed, funding, hour, market_regime, pattern, regime_confidence, trend, volatility, volume, weekday`
- **EMPTY**: 14 — `ai_score, book_imbalance, btc_move, confidence, decision_confidence, etf_flow, liquidation_metric, macd, macro_score, news_category, news_score, shock_score, spread, time_to_expiry`
- **GOOD**: 5 — `btc_dominance, gate_expected_pnl_pct, oi_delta, similar_count, symbol`
- **LOW_VARIANCE**: 3 — `gate_decision, open_interest, regime_score`

## Features

| feature | filled% | nulls | const% | unique | min | max | median | std | entropy | MI|pnl | corr|pnl | status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ema200_distance | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **BROKEN** |
| ema20_distance | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **BROKEN** |
| ema50_distance | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **BROKEN** |
| rsi | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **BROKEN** |
| vwap_distance | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **BROKEN** |
| ai_score | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| book_imbalance | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| btc_move | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| confidence | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| decision_confidence | 0.0 | 150 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| etf_flow | 0.0 | 150 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| liquidation_metric | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| macd | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| macro_score | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| news_category | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| news_score | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| shock_score | 0.0 | 150 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| spread | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| time_to_expiry | 0.0 | 50 | 0.0 | 0 | None | None | None | None | None | None | None | **EMPTY** |
| atr | 100.0 | 0 | 100.0 | 1 | 50.0 | 50.0 | 50.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| direction | 100.0 | 0 | 100.0 | 1 | None | None | None | None | 0.0 | None | None | **CONSTANT** |
| ema_trend | 100.0 | 0 | 100.0 | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| fear_greed | 100.0 | 0 | 100.0 | 1 | 22.0 | 22.0 | 22.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| funding | 100.0 | 0 | 100.0 | 1 | 54.1 | 54.1 | 54.1 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| hour | 100.0 | 0 | 100.0 | 1 | 21.0 | 21.0 | 21.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| market_regime | 100.0 | 0 | 100.0 | 1 | None | None | None | None | 0.0 | None | None | **CONSTANT** |
| pattern | 100.0 | 0 | 100.0 | 1 | None | None | None | None | 0.0 | None | None | **CONSTANT** |
| regime_confidence | 100.0 | 0 | 100.0 | 1 | 0.95 | 0.95 | 0.95 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| trend | 100.0 | 0 | 100.0 | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| volatility | 100.0 | 0 | 100.0 | 1 | 50.0 | 50.0 | 50.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| volume | 100.0 | 0 | 100.0 | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| weekday | 100.0 | 0 | 100.0 | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | **CONSTANT** |
| gate_decision | 100.0 | 0 | 90.0 | 2 | None | None | None | None | 0.469 | None | None | **LOW_VARIANCE** |
| open_interest | 100.0 | 0 | 86.67 | 2 | 72.0 | 72.0 | 72.0 | 0.0 | 0.5665 | 0.0 | 0.0 | **LOW_VARIANCE** |
| regime_score | 100.0 | 0 | 66.67 | 2 | -0.255 | -0.255 | -0.255 | 0.0 | 0.9183 | 0.0 | 0.0 | **LOW_VARIANCE** |
| btc_dominance | 100.0 | 0 | 66.67 | 11 | 49.69 | 50.19 | 50.02 | 0.113988 | 1.6915 | 0.0979 | -0.1341 | **GOOD** |
| gate_expected_pnl_pct | 100.0 | 0 | 66.67 | 13 | -7.9557 | 0.0 | -0.7097 | 3.133329 | 1.7588 | 0.1213 | -0.0436 | **GOOD** |
| oi_delta | 100.0 | 0 | 50.0 | 11 | -54351.762 | 72.0 | -26872.95 | 27116.595412 | 2.4419 | 0.1102 | -0.0248 | **GOOD** |
| similar_count | 100.0 | 0 | 66.67 | 13 | 0.0 | 45.0 | 3.0 | 13.465452 | 1.7588 | 0.1092 | 0.0478 | **GOOD** |
| symbol | 100.0 | 0 | 6.0 | 20 | None | None | None | None | 4.2929 | None | None | **GOOD** |

## Formula sources

### RSI — `BROKEN SOURCE`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features`
- table: `market_events_trade_features_s55 / features_json`
- collector: `None`
- notes: Hardcoded rsi=None in build_entry_features — never computed from candles.

### EMA — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features (ema_trend proxy from snapshot_trend)`
- table: `market_snapshots_g3 / S40 snapshot_trend`
- collector: `g3 snapshot / S40 signal snapshot`
- notes: True EMA levels not written; only ema_trend proxy from trend string/float.

### VWAP — `BROKEN SOURCE`
- module: `bot.research.market_events.signal_intelligence.feature_store`
- function: `extract_sample (_dist from vwap)`
- table: `None`
- collector: `None`
- notes: VWAP not populated in S55/S40 — distances always NULL in Feature Store.

### ATR — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features (from S40 snapshot_atr or market_snapshots_g3.atr)`
- table: `market_snapshots_g3.atr / S40.snapshot_atr`
- collector: `g3-run / market snapshot collector`
- notes: volatility aliased to atr; observed CONSTANT placeholder values in S55.

### Funding — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features`
- table: `market_snapshots_g3.funding`
- collector: `g3 snapshot / Bybit (or configured) funding feed`
- notes: From S40 snapshot_funding or latest market_snapshots_g3.funding.

### OI — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features (oi - prev_oi from last 2 snapshots)`
- table: `market_snapshots_g3.open_interest`
- collector: `g3 snapshot`
- notes: Often falls back to raw OI when prev missing; cross-symbol identical deltas observed.

### Fear — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features`
- table: `market_snapshots_g3.fear_greed`
- collector: `macro / fear&greed collector`
- notes: From S40 snapshot_fear_greed or market_snapshots_g3.fear_greed.

### Trend — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features`
- table: `S40.snapshot_trend / features_json.trend`
- collector: `trend / MTF pipeline`
- notes: From S40 snapshot_trend; often 0.0 constant in closed book.

### AI score — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `build_entry_features (ai_score = decision_confidence alias)`
- table: `S40.snapshot_decision_confidence`
- collector: `candidate / decision engine`
- notes: Not a separate model score — alias of snapshot_decision_confidence; often NULL.

### Neighbor EV — `OK`
- module: `bot.research.market_events.signal_intelligence.trade_intelligence_s55`
- function: `estimate_from_similar / gate path`
- table: `market_events_trade_features_s55.gate_expected_pnl_pct`
- collector: `None`
- notes: Neighbor EV used for S55 gate; stored on S55 columns.

### Regime — `OK`
- module: `bot.research.market_events.signal_intelligence.market_regime_s57`
- function: `regime classification applied into S55 features`
- table: `market_events_trade_features_s55.market_regime`
- collector: `None`
- notes: S57 regime; closed book observed as 100% RANGE.


## Live table freshness

- `market_snapshots_g3`: {'last_update': 1785515279, 'age_sec': 62, 'status': 'OK'}
- `market_events_signal_learning_s40_signals`: {'last_update': 1785514974, 'age_sec': 367, 'status': 'OK'}
- `market_events_trade_features_s55`: {'last_update': 1785515340, 'age_sec': 1, 'status': 'OK'}
- `market_events_paper_trades_s42`: {'last_update': 1785361779, 'age_sec': 153562, 'status': 'STALE'}
- `market_candidate_g31`: {'last_update': 1785515326, 'age_sec': 15, 'status': 'OK'}
- `market_events_historical_candles`: {'last_update': 1785515100, 'age_sec': 241, 'status': 'OK'}
