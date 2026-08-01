# RESEARCH_LAKE_REPORT

_Research Lake Builder V2 — streaming batches, preloaded joins, SQL profiling. No Gate / Optimizer / Strategy / Paper / Execution changes._

- Mode: **full**
- Builder: `v2-streaming`
- Rows seen/inserted/updated/skipped: 19160/19110/0/50
- dataset_version: `rlake-v1`
- feature_version: `v1`
- schema_version: `1.0.0`
- Health: **WARN** (lake=19160 s42_closed=19160 coverage=100.0%)
- Elapsed: 15.79s
- no_select_in_trade_loop: True

## Integrity

- duplicates: 0
- missing_s55_joins: 19110
- missing_pnl: 0
- null_feature_rows: 500
- broken_feature_rows: 0
- schema_drift: []

## Issues

- missing_s55_joins=19110
- null_explosion=500/500

## Consumers

Alpha / Optimizer / ML / Feature Information / Math Research / Edge Discovery should load via `load_research_lake_rows` (Research Lake only).

Artifacts under `reports/research/lake/`.
