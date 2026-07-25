# Canonical Research Dataset Schema (Phase 5B)

Export layer only. Open-time features per closed trade.
Leakage columns are rejected at build time.

- Rows: **1786**
- Feature columns: **75**
- Identity columns: **5**
- Rejected leakage names (unique): **33**

## Identity keys

Used to join labels and upstream tables:

- `opened_at`
- `paper_trade_id`
- `row_id`
- `s40_signal_id`
- `s40_signal_type`

## Target variables (labels — NOT in feature matrix)

Labels are written to `metadata.json` → `labels` (and summarized here).
Join back on `(paper_trade_id)` or `(s40_signal_type, s40_signal_id)`.

| Label | Meaning | Source |
|-------|---------|--------|
| `label_pnl_usd` | Closed PnL USD | S56 |
| `label_pnl_pct` | Closed PnL % | S56 |
| `label_win` | 1 if pnl_usd > 0 else 0 | derived |
| `label_exit_reason` | Exit reason (forensics) | S56 |
| `label_duration_sec` | Hold duration | S56 |

_Label rows attached in metadata: 1786_

## Categorical features

- `derived_coin` — fill=100.0% source=`derived` dtype=str
- `derived_is_hist` — fill=100.0% source=`derived` dtype=bool
- `derived_session` — fill=100.0% source=`derived` dtype=str
- `feat_market_slug` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=str
- `feat_regime_label` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `feat_side` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=str
- `feat_source_table` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=str
- `feat_strategy_name` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=str
- `s56_atr` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_direction` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56_etf_flow` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_expected_pnl_pct` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_fear_greed` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_funding` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_hour` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int
- `s56_macro_score` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_market_regime` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_market_score` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_news_score` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_oi_delta` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_symbol` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56_tp1` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_tp2` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_trend` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_volume` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_vwap` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56_weekday` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int
- `s56j_atr` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_direction` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56j_etf_flow` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_expected_pnl_pct` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_fear_greed` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_funding` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_hour` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int
- `s56j_macro_score` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_market_regime` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_market_score` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_market_slug` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56j_news_score` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_oi_delta` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_source_db` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56j_source_table` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56j_strategy` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56j_symbol` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=str
- `s56j_tp1` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_tp2` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_trend` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_volume` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_vwap` — fill=0.0% source=`market_events_trade_snapshots_s56` dtype=null
- `s56j_weekday` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int

## Continuous features

- `derived_ai_score_0_100` — fill=5.991% source=`derived` dtype=float
- `feat_ask` — fill=28.6114% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_bid` — fill=28.6114% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_distance_to_strike` — fill=28.6114% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_entry_price` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_entry_ts` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=int
- `feat_spread` — fill=28.6114% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_stop_loss_pct` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=int
- `feat_trailing_activation` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_trailing_distance` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_volatility_15s` — fill=28.1635% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_volatility_30s` — fill=28.6114% source=`market_events_trade_snapshots_s56` dtype=float
- `feat_volatility_60s` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=float
- `s56_ai_score` — fill=5.991% source=`market_events_trade_snapshots_s56` dtype=float
- `s56_entry` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=float
- `s56_spread` — fill=28.6114% source=`market_events_trade_snapshots_s56` dtype=float
- `s56_timestamp` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int
- `s56_volatility` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=float
- `s56j_ai_score` — fill=5.991% source=`market_events_trade_snapshots_s56` dtype=float
- `s56j_created_at` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int
- `s56j_entry` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=float
- `s56j_entry_ts` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int
- `s56j_spread` — fill=28.6114% source=`market_events_trade_snapshots_s56` dtype=float
- `s56j_timestamp` — fill=100.0% source=`market_events_trade_snapshots_s56` dtype=int
- `s56j_volatility` — fill=28.7234% source=`market_events_trade_snapshots_s56` dtype=float

## Full feature list

| name | dtype | fill_rate | source_table | available_at_open |
|------|-------|----------:|--------------|-------------------|
| `derived_ai_score_0_100` | float | 5.991 | `derived` | true |
| `derived_coin` | str | 100.0 | `derived` | true |
| `derived_is_hist` | bool | 100.0 | `derived` | true |
| `derived_session` | str | 100.0 | `derived` | true |
| `feat_ask` | float | 28.6114 | `market_events_trade_snapshots_s56` | true |
| `feat_bid` | float | 28.6114 | `market_events_trade_snapshots_s56` | true |
| `feat_distance_to_strike` | float | 28.6114 | `market_events_trade_snapshots_s56` | true |
| `feat_entry_price` | float | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_entry_ts` | int | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_market_slug` | str | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_regime_label` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `feat_side` | str | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_source_table` | str | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_spread` | float | 28.6114 | `market_events_trade_snapshots_s56` | true |
| `feat_stop_loss_pct` | int | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_strategy_name` | str | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_trailing_activation` | float | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_trailing_distance` | float | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `feat_volatility_15s` | float | 28.1635 | `market_events_trade_snapshots_s56` | true |
| `feat_volatility_30s` | float | 28.6114 | `market_events_trade_snapshots_s56` | true |
| `feat_volatility_60s` | float | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `s56_ai_score` | float | 5.991 | `market_events_trade_snapshots_s56` | true |
| `s56_atr` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_direction` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56_entry` | float | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56_etf_flow` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_expected_pnl_pct` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_fear_greed` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_funding` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_hour` | int | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56_macro_score` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_market_regime` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_market_score` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_news_score` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_oi_delta` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_spread` | float | 28.6114 | `market_events_trade_snapshots_s56` | true |
| `s56_symbol` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56_timestamp` | int | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56_tp1` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_tp2` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_trend` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_volatility` | float | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `s56_volume` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_vwap` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56_weekday` | int | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_ai_score` | float | 5.991 | `market_events_trade_snapshots_s56` | true |
| `s56j_atr` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_created_at` | int | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_direction` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_entry` | float | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_entry_ts` | int | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_etf_flow` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_expected_pnl_pct` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_fear_greed` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_funding` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_hour` | int | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_macro_score` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_market_regime` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_market_score` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_market_slug` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_news_score` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_oi_delta` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_source_db` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_source_table` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_spread` | float | 28.6114 | `market_events_trade_snapshots_s56` | true |
| `s56j_strategy` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_symbol` | str | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_timestamp` | int | 100.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_tp1` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_tp2` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_trend` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_volatility` | float | 28.7234 | `market_events_trade_snapshots_s56` | true |
| `s56j_volume` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_vwap` | null | 0.0 | `market_events_trade_snapshots_s56` | true |
| `s56j_weekday` | int | 100.0 | `market_events_trade_snapshots_s56` | true |

## Rejected leakage (examples)

- `s56.snapshot_json.exit_price`
- `s56.snapshot_json.pnl_usd`
- `s56.snapshot_json.pnl_pct`
- `s56.snapshot_json.duration_sec`
- `s56.snapshot_json.exit_reason`
- `s56.snapshot_json.trailing`
- `s56.snapshot_json.exit_ts`
- `s56.snapshot_json.mae`
- `s56.snapshot_json.mfe`
- `s56.snapshot_json.status`
- `s56.features.trade_id`
- `s56.features.exit_price`
- `s56.features.pnl`
- `s56.features.pnl_usdc`
- `s56.features.btc_move_5s`
- `s56.features.btc_move_10s`
- `s56.features.btc_move_15s`
- `s56.features.btc_move_20s`
- `s56.features.btc_move_30s`
- `s56.features.btc_move_45s`
- `s56.features.btc_move_60s`
- `s56.features.btc_move_90s`
- `s56.features.seconds_open`
- `s56.features.holding_time`
- `s56.features.mfe`
- `s56.features.mae`
- `s56.features.is_win`
- `s56.features.is_loss`
- `s56.features.is_stop`
- `s56.features.is_time_stop`
- `s56.features.is_trailing`
- `s56.features.exit_reason`
- `s56.features.built_at`

## Future consumers

This dataset is the **intended sole feature input** for S59 / S61 / S62 / S64 / S65.
Existing reports remain on `load_lab_trades()` until explicitly migrated.

## Build

```bash
python -m bot.research.market_events.research_dataset_builder
```

