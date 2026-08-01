# RESEARCH_LAKE_REPORT

_Research Lake Builder V2 — streaming batches, preloaded joins, SQL profiling. No Gate / Optimizer / Strategy / Paper / Execution changes._

- Mode: **full**
- Builder: `v2-streaming`
- Rows seen/inserted/updated/skipped: 50/0/0/50
- dataset_version: `rlake-v1`
- feature_version: `v1`
- schema_version: `1.0.0`
- Health: **FAIL** (lake=50 s42_closed=50 coverage=100.0%)
- Elapsed: 1.014s
- no_select_in_trade_loop: True

## Integrity

- duplicates: 0
- missing_s55_joins: 0
- missing_pnl: 0
- null_feature_rows: 0
- broken_feature_rows: 50
- schema_drift: []

## Issues

- broken_features=50

## Consumers

Alpha / Optimizer / ML / Feature Information / Math Research / Edge Discovery should load via `load_research_lake_rows` (Research Lake only).

Artifacts under `reports/research/lake/`.
