# Feature Information V1

_samples=50 | shap_backend=sklearn_rf_impurity_proxy_

## Ranking buckets

- **USEFUL** (3): `symbol, oi_delta, gate_decision`
- **WEAK** (0): ``
- **NOISE** (0): ``
- **REMOVE** (27): `direction, pattern, news_category, market_regime, confidence, ai_score, macro_score, news_score, volatility, atr, vwap_distance, ema20_distance, ema50_distance, ema200_distance, rsi, macd, funding, liquidation_metric, spread, book_imbalance, time_to_expiry, btc_move, volume, fear_greed, trend, hour, weekday`

## TOP 20

| rank | feature | score | IG | MI | AUC | EV | PF | perm | bucket |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | symbol | 0.1696 | 0.0004 | 0.4043 | 0.4054 | -23.0667 | 0.4489 | None | USEFUL |
| 2 | oi_delta | 0.0795 | 0.005 | 0.1102 | 0.6137 | -22.9688 | 0.5067 | 0.0373 | USEFUL |
| 3 | gate_decision | 0.0396 | 0.0191 | 0.0191 | 0.5521 | 2.5435 | 1.0553 | None | USEFUL |
| 4 | direction | 0.0 | 0.0 | 0.0 | 0.5 | -25.2943 | 0.4379 | None | REMOVE |
| 5 | pattern | 0.0 | 0.0 | 0.0 | 0.5 | -25.2943 | 0.4379 | None | REMOVE |
| 6 | news_category | -1.0 | None | None | None | None | None | None | REMOVE |
| 7 | market_regime | 0.0 | 0.0 | 0.0 | 0.5 | -25.2943 | 0.4379 | None | REMOVE |
| 8 | confidence | -1.0 | None | None | None | None | None | None | REMOVE |
| 9 | ai_score | -1.0 | None | None | None | None | None | None | REMOVE |
| 10 | macro_score | -1.0 | None | None | None | None | None | None | REMOVE |
| 11 | news_score | -1.0 | None | None | None | None | None | None | REMOVE |
| 12 | volatility | 0.0 | 0.0 | 0.0 | 0.5 | -25.2943 | 0.4379 | None | REMOVE |
| 13 | atr | 0.0 | 0.0 | 0.0 | 0.5 | -25.2943 | 0.4379 | None | REMOVE |
| 14 | vwap_distance | -1.0 | None | None | None | None | None | None | REMOVE |
| 15 | ema20_distance | -1.0 | None | None | None | None | None | None | REMOVE |
| 16 | ema50_distance | -1.0 | None | None | None | None | None | None | REMOVE |
| 17 | ema200_distance | -1.0 | None | None | None | None | None | None | REMOVE |
| 18 | rsi | -1.0 | None | None | None | None | None | None | REMOVE |
| 19 | macd | -1.0 | None | None | None | None | None | None | REMOVE |
| 20 | funding | 0.0 | 0.0 | 0.0 | 0.5 | -25.2943 | 0.4379 | None | REMOVE |

## TOP combinations (by EV, max 20 shown)

- `oi_delta+gate_decision` n=5 EV=2.5435 PF=1.0553 WR=60.0 CI=(-93.1305, 80.4227) p=0.5025
- `symbol+oi_delta` n=13 EV=-19.7345 PF=0.5505 WR=30.77 CI=(-77.2003, 27.2436) p=0.8209

## ML dataset QA

- **CRITICAL: constant/empty columns: direction, pattern, news_category(all_null), market_regime, confidence(all_null), ai_score(all_null), macro_score(all_null), news_score(all_null), volatility, atr, vwap_distance(all_null), ema20_distance(all_null), ema50_distance(all_null), ema200_distance(all_null), rsi(all_null), macd(all_null), funding, liquidation_metric(all_null), spread(all_null), book_imbalance(all_null)**
- warning: mfe/mae present on samples (labels/aux — ensure excluded from predictors)
