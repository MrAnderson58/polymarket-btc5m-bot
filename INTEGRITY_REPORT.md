# INTEGRITY_REPORT

_Research Integrity Fix V1 — ONE canonical dataset before forward validation._

- runtime: **6.295s**
- all_ok: **True**

## FIX 1 — Reality Validation (dataset parity)
- ok: True
- dataset_version: `rlake-v1`
- lake_rows: 19205
- build_ts: 1785657926
- hash: `306cb3f707eb371f...`
- stored_score: 85.98
- recompute_score: 85.98
- mismatches: []

## FIX 2 — Elite canonical table
- ok: True
- table: `elite_candidates_v1`
- n_elite: 4111
- issues: []

## FIX 3 — S55 / Feature Store audit
- NO_S55_RECORD: **0**
- TIMESTAMP_MISMATCH: 0
- unexpected_s55: **0**
- n_s55 rows: 19427
- feature_store_ok: True
- root_causes: []

### Pipeline
```json
{
  "generation_stage": "S42 paper trade close \u2192 S55 feature row (collector)",
  "lake_build": "research_lake_build joins S55 via paper_trade_id / s40 keys",
  "feature_store_build": "sync_feature_store extracts closed trades \u2192 parquet",
  "join_key_primary": "paper_trade_id",
  "join_key_fallback": "(s40_signal_type, s40_signal_id)",
  "timestamp_key": "closed_at (\u00b1300s fuzzy audit, \u00b160s auto-reconcile)"
}
```

## FIX 4 — TIMESTAMP_MISMATCH reconcile (<60s)
- before: 0
- reconciled: 0
- after: 0
- remaining: 45

## FIX 5 — Book D Feature Store gate
- ok: True
- book_d_will_refuse: False
- n_samples: 50

research_freeze=true observe_only=true
