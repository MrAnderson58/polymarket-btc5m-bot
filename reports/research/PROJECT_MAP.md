# Project Map — Signal Mathematics Recovery

```
collector → table → feature → research → optimizer → validation → ml
```

## g3 snapshot / market_data collectors
- table: `market_snapshots_g3`
- fields: `['funding', 'open_interest', 'atr', 'fear_greed', 'volume', 'btc_dominance', 'btc_price']`
- features: `['funding', 'oi_delta', 'atr', 'volatility', 'fear_greed', 'volume']`
- research: `['trade_intelligence_s55.build_entry_features', 'pipeline_audit_g0', 'score_breakdown_g34']`
- optimizer: `indirect via S55`
- validation: `indirect`
- ml: `['funding', 'atr', 'fear_greed', 'volume', 'oi_delta']`
- usage: **used**
- live statuses: `{'funding': 'CONSTANT', 'oi_delta': 'GOOD', 'atr': 'CONSTANT', 'volatility': 'CONSTANT', 'fear_greed': 'CONSTANT', 'volume': 'CONSTANT'}`

## S40 signal learning / candidate path
- table: `market_events_signal_learning_s40_signals`
- fields: `['snapshot_funding', 'snapshot_atr', 'snapshot_fear_greed', 'snapshot_trend', 'snapshot_news_score', 'snapshot_decision_confidence']`
- features: `['funding', 'atr', 'fear_greed', 'trend', 'news_score', 'confidence', 'ai_score']`
- research: `['trade_intelligence_s55', 'signal learning reviews']`
- optimizer: `confidence gate inputs (when present)`
- validation: `FilterParams.confidence_threshold`
- ml: `['confidence', 'ai_score', 'news_score', 'trend']`
- usage: **used**
- live statuses: `{'funding': 'CONSTANT', 'atr': 'CONSTANT', 'fear_greed': 'CONSTANT', 'trend': 'CONSTANT', 'news_score': 'EMPTY', 'confidence': 'EMPTY', 'ai_score': 'EMPTY'}`

## trade_intelligence_s55 gate + feature log
- table: `market_events_trade_features_s55`
- fields: `['rsi', 'atr', 'funding', 'oi_delta', 'market_regime', 'gate_decision', 'features_json']`
- features: `['rsi', 'atr', 'funding', 'oi_delta', 'market_regime', 'gate_decision']`
- research: `['feature_store', 'strategy_optimizer', 'feature_validation_v1', 'math_recovery_v1']`
- optimizer: `segment reports via join`
- validation: `ab_replay universe join`
- ml: `FEATURE_SPEC numerics`
- usage: **used**
- live statuses: `{'rsi': 'BROKEN', 'atr': 'CONSTANT', 'funding': 'CONSTANT', 'oi_delta': 'GOOD', 'market_regime': 'CONSTANT', 'gate_decision': 'LOW_VARIANCE'}`

## S42 paper runner
- table: `market_events_paper_trades_s42`
- fields: `['pnl_usd', 'pnl_pct', 'mfe_pct', 'mae_pct', 'holding_seconds', 'decision_confidence']`
- features: `['pnl', 'mfe', 'mae', 'holding_time', 'confidence']`
- research: `['optimizer', 'validation', 'experiments', 'feature_store']`
- optimizer: `primary`
- validation: `primary`
- ml: `labels + confidence`
- usage: **used**
- live statuses: `{'confidence': 'EMPTY'}`

## G31 candidate engine
- table: `market_candidate_g31`
- fields: `['confidence', 'fear_greed', 'atr_score', 'volume_score', 'rejection_reason']`
- features: `['candidate confidence / scores (not always copied to S55)']`
- research: `['auto_validation_g4', 'research_dataset_g50', 'pipeline flow']`
- optimizer: `not directly`
- validation: `not directly`
- ml: `not in Feature Store V1`
- usage: **partially_used**

## NONE (missing producer)
- table: `None`
- fields: `['rsi candle series']`
- features: `['rsi']`
- research: `['feature_store expects rsi']`
- optimizer: `unused (always null)`
- validation: `unused`
- ml: `column present but EMPTY`
- usage: **never_read_from_live_source**
- dead_code: `rsi=None hardcoded in build_entry_features`
- live statuses: `{'rsi': 'BROKEN'}`

## NONE (missing producer)
- table: `None`
- fields: `['ema20', 'ema50', 'ema200', 'vwap']`
- features: `['ema*_distance', 'vwap_distance']`
- research: `['feature_store.extract_sample']`
- optimizer: `unused`
- validation: `unused`
- ml: `EMPTY distances`
- usage: **never_read_from_live_source**
- dead_code: `distance derivation dead without level inputs`
- live statuses: `{'vwap_distance': 'BROKEN'}`

## market_regime_s57
- table: `S55.market_regime`
- fields: `['market_regime', 'regime_score']`
- features: `['market_regime']`
- research: `['gate funnel', 'optimizer segments', 'experiments']`
- optimizer: `symbol/regime segments`
- validation: `indirect`
- ml: `categorical`
- usage: **used**
- live statuses: `{'market_regime': 'CONSTANT'}`

## research-only experiment / hypothesis engines
- table: `research_experiments / experiment_runs`
- fields: `['ev_after', 'pf_after']`
- features: `[]`
- research: `['hypothesis-validate', 'experiment-run']`
- optimizer: `separate from Adaptive Optimizer V1`
- validation: `separate`
- ml: `no`
- usage: **used**
