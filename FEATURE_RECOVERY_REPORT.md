# FEATURE_RECOVERY_REPORT

_Signal Feature Recovery V2 — Gate/Trading/Paper/Execution unchanged._

- S42 closed corpus available locally: **50**
- Info matrix samples used: **50**
- Recovery % (GOOD with live value): **91.3%**

## Recovered Features

- `rsi` live=48.6082
- `ema20_distance` live=-0.043917
- `ema50_distance` live=-0.024258
- `ema200_distance` live=-0.853004
- `vwap_distance` live=0.15819
- `atr` live=76.87857143
- `atr_pct` live=0.122017
- `macd` live=19.44923358
- `macd_hist` live=-15.86966245
- `bb_pct_b` live=0.25
- `adx` live=15.7312
- `stoch_k` live=29.5409
- `trend` live=0.0479
- `slope` live=-0.012511
- `funding` live=2.5e-05
- `funding_delta` live=2.9e-07
- `open_interest` live=58089.314
- `oi_delta` live=7.484
- `fear_greed` live=25.0
- `volume` live=203.5484
- `volatility` live=0.122017

## Still Broken

- `ai_score`

## Fresh Collectors

- `rsi` age=263s
- `ema20_distance` age=263s
- `ema50_distance` age=263s
- `ema200_distance` age=263s
- `vwap_distance` age=263s
- `atr` age=263s
- `atr_pct` age=263s
- `macd` age=263s
- `macd_hist` age=263s
- `bb_pct_b` age=263s
- `adx` age=263s
- `stoch_k` age=263s
- `trend` age=263s
- `slope` age=263s
- `funding` age=185s
- `funding_delta` age=185s
- `open_interest` age=185s
- `oi_delta` age=185s
- `fear_greed` age=185s
- `volume` age=263s
- `volatility` age=263s

## Dead Collectors

- `ai_score`: BROKEN_SOURCE_UNTIL_AI_PRODUCER — NONE (no longer aliases confidence)

## Constant Features (live completeness)

- `funding`
- `funding_delta`
- `open_interest`
- `oi_delta`
- `fear_greed`

## Ranking (predictive power on available S42)

1. `symbol` score=0.1696 IG=0.0004 MI=0.4043 AUC=0.4054 EV=-23.0667 PF=0.4489 bucket=USEFUL
2. `oi_delta` score=0.0795 IG=0.005 MI=0.1102 AUC=0.6137 EV=-22.9688 PF=0.5067 bucket=USEFUL
3. `gate_decision` score=0.0396 IG=0.0191 MI=0.0191 AUC=0.5521 EV=2.5435 PF=1.0553 bucket=USEFUL
4. `direction` score=0.0 IG=0.0 MI=0.0 AUC=0.5 EV=-25.2943 PF=0.4379 bucket=REMOVE
5. `pattern` score=0.0 IG=0.0 MI=0.0 AUC=0.5 EV=-25.2943 PF=0.4379 bucket=REMOVE
6. `news_category` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
7. `market_regime` score=0.0 IG=0.0 MI=0.0 AUC=0.5 EV=-25.2943 PF=0.4379 bucket=REMOVE
8. `confidence` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
9. `ai_score` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
10. `macro_score` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
11. `news_score` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
12. `volatility` score=0.0 IG=0.0 MI=0.0 AUC=0.5 EV=-25.2943 PF=0.4379 bucket=REMOVE
13. `atr` score=0.0 IG=0.0 MI=0.0 AUC=0.5 EV=-25.2943 PF=0.4379 bucket=REMOVE
14. `vwap_distance` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
15. `ema20_distance` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
16. `ema50_distance` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
17. `ema200_distance` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
18. `rsi` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
19. `macd` score=-1.0 IG=None MI=None AUC=None EV=None PF=None bucket=REMOVE
20. `funding` score=0.0 IG=0.0 MI=0.0 AUC=0.5 EV=-25.2943 PF=0.4379 bucket=REMOVE

## Live BTC proof (not stubs)

- `rsi` = `48.6082`
- `ema20_distance` = `-0.043917`
- `ema50_distance` = `-0.024258`
- `ema200_distance` = `-0.853004`
- `vwap_distance` = `0.15819`
- `atr` = `76.87857143`
- `atr_pct` = `0.122017`
- `macd` = `19.44923358`
- `bb_pct_b` = `0.25`
- `adx` = `15.7312`
- `stoch_k` = `29.5409`
- `trend` = `0.0479`
- `funding` = `2.5e-05`
- `oi_delta` = `7.484`
- `fear_greed` = `25.0`
- `volatility` = `0.122017`
- `ai_score` = `None`
- candle_source=`market_events_historical_candles` n=`220`

## Recommendations

1. Ensure paper opens call `build_entry_features` so V2 enrichment persists into S55.
2. Backfill RSI/EMA/VWAP onto historical S55 rows for research on full corpus.
3. Wire a real AI score producer (ai_score is intentionally not aliased to confidence).
4. When S42 grows past 30k on the research host, re-run `feature-information` with ML_STORE_LIMIT=100000.
