# REPLAY_REPORT

_Market Replay Engine V1 — reconstruct CLOSED trades as candle movies. Research only. Gate / Strategy / Paper / Optimizer / Execution unchanged._

## Run

- trades analyzed: **50**
- replays built: **50**
- coverage (≥50% quality): **100.0%**
- mean quality: **0.5**
- runtime: **0.967s**
- lake source: `research_lake_v1`
- candles preloaded: 0
- G3 snapshots: 0

## Missing data report

```json
{
  "oi": 1.0,
  "funding_delta": 1.0,
  "ema20": 1.0,
  "ema50": 1.0,
  "ema200": 1.0,
  "vwap": 1.0,
  "macd": 1.0,
  "adx": 1.0,
  "rsi": 1.0,
  "stochastic": 1.0,
  "news_score": 1.0,
  "ai_score": 1.0
}
```

## Top recurring replay patterns

- n=45 `RANGE|LONG|INSUFFICIENT_HISTORY|-|vol-|oi-` examples=[3, 9, 13, 14, 17, 23, 7, 19, 29, 33]
- n=5 `RANGE|LONG|REGIME_EXPLORE|-|vol-|oi-` examples=[46, 47, 48, 49, 50]

## Replay examples

### trade_id=3

- quality: 0.5
- frames: ['T-60m', 'T-30m', 'T-15m', 'T-10m', 'T-5m', 'T-3m', 'T-1m', 'ENTRY', '+1m', '+3m', '+5m', '+10m', '+15m', '+30m', '+60m']
- ENTRY price=0.08917 rsi=None funding=54.1 regime=RANGE
- liquidity: `{"orderbook_imbalance": null, "volume_expansion": null, "oi_expansion": null, "liquidation_clusters": 0.0, "volatility_expansion": 0.0, "ok": true}`

### trade_id=9

- quality: 0.5
- frames: ['T-60m', 'T-30m', 'T-15m', 'T-10m', 'T-5m', 'T-3m', 'T-1m', 'ENTRY', '+1m', '+3m', '+5m', '+10m', '+15m', '+30m', '+60m']
- ENTRY price=4.93 rsi=None funding=54.1 regime=RANGE
- liquidity: `{"orderbook_imbalance": null, "volume_expansion": null, "oi_expansion": null, "liquidation_clusters": 0.0, "volatility_expansion": 0.0, "ok": true}`

### trade_id=13

- quality: 0.5
- frames: ['T-60m', 'T-30m', 'T-15m', 'T-10m', 'T-5m', 'T-3m', 'T-1m', 'ENTRY', '+1m', '+3m', '+5m', '+10m', '+15m', '+30m', '+60m']
- ENTRY price=2.003 rsi=None funding=54.1 regime=RANGE
- liquidity: `{"orderbook_imbalance": null, "volume_expansion": null, "oi_expansion": null, "liquidation_clusters": 0.0, "volatility_expansion": 0.0, "ok": true}`

## Integrity

- gate_unchanged: True
- paper_unchanged: True
- optimizer_unchanged: True
- execution_unchanged: True
- no_n_plus_1_sql: True
