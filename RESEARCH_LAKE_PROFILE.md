# RESEARCH_LAKE_PROFILE

Research Lake Builder V2 — SQL profile for streaming builds.

## Summary

- builder_version: `v2-streaming`
- mode: `full`
- rows_seen / inserted / updated / skipped: 50 / 0 / 0 / 50
- batch_size: 1000
- elapsed_sec (wall): 1.014
- n_queries profiled: 12
- total_sql_sec: 0.9657
- n_slow (>=1s): 0
- no_select_in_trade_loop: True

## Before / After

| Metric | V1 (N+1 per trade) | V2 (streaming + preload) |
|---|---|---|
| Join strategy | SELECT S55/S56/G31/alpha inside trade loop | Preload dicts once; O(1)/O(log n) lookup |
| S42 load | Full table / unbounded | `WHERE id > last_id LIMIT 1000` batches |
| Inserts | Single end commit (or per-row) | Upsert + commit every 1000 rows |
| Indexes | Missing on `s55.paper_trade_id` etc. | Auto `CREATE INDEX IF NOT EXISTS` on JOIN keys |
| Wall time (this run) | (N× queries → minutes in `sqlite3_step`) | **1.014s** |
| 30k synthetic wall | V1 N+1: minutes–hours at scale | **V2 1.903s** (under_2_min=True) |

Target: 30k CLOSED trades under 2 minutes on Apple Silicon.

## Indexes added / ensured

- `idx_s55_paper_trade_id`
- `idx_s56_paper_trade_id`
- `idx_s42_status_id`
- `idx_s42_closed_pnl_id`
- `idx_g31_symbol_created`
- `idx_rlake_v1_row_hash`

## Top 20 slow SQL

1. **0.9622s** rows=61220 params=``

```sql
SELECT id, symbol, direction, market_score, confidence, created_at FROM market_candidate_g31
```

2. **0.0014s** rows=50 params=``

```sql
SELECT * FROM market_events_trade_features_s55 WHERE paper_trade_id IS NOT NULL ORDER BY id ASC
```

3. **0.0007s** rows=50 params=`(0, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

4. **0.0003s** rows=50 params=``

```sql
SELECT trade_id, row_hash FROM market_events_research_lake_v1
```

5. **0.0003s** rows=1 params=`()`

```sql
SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL
```

6. **0.0003s** rows=0 params=``

```sql
SELECT * FROM market_events_trade_snapshots_s56 WHERE paper_trade_id IS NOT NULL ORDER BY id ASC
```

7. **0.0002s** rows=6 params=``

```sql
-- ensure_research_lake_join_indexes
```

8. **0.0001s** rows=0 params=``

```sql
SELECT trade_id, paper_trade_id, validation_status, score, rule_id FROM market_events_alpha_validations_v2
```

9. **0.0s** rows=0 params=``

```sql
SELECT key, value FROM market_events_ops_state WHERE key LIKE 'optimizer%' OR key LIKE 'g42%' LIMIT 20
```

10. **0.0s** rows=0 params=``

```sql
-- ensure_research_lake_schema
```

11. **0.0s** rows=0 params=``

```sql
SELECT id, name, status FROM market_events_experiments_v1 ORDER BY id DESC LIMIT 5
```

12. **0.0s** rows=0 params=``

```sql
SELECT trade_id, cluster, edge_score, status FROM market_events_alpha_labels_v1
```


## EXPLAIN QUERY PLAN (slow >= 1s, else top wall-time)

### #1 — 0.9622s (rows=61220)

```sql
SELECT id, symbol, direction, market_score, confidence, created_at FROM market_candidate_g31
```

Plan:

- `2 | 0 | 0 | SCAN market_candidate_g31`

### #2 — 0.0014s (rows=50)

```sql
SELECT * FROM market_events_trade_features_s55 WHERE paper_trade_id IS NOT NULL ORDER BY id ASC
```

Plan:

- `3 | 0 | 0 | SCAN market_events_trade_features_s55`

### #3 — 0.0007s (rows=50)

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

Plan:

- `7 | 0 | 0 | SEARCH market_events_paper_trades_s42 USING INDEX idx_s42_closed_pnl_id (status=? AND id>?)`

### #4 — 0.0003s (rows=50)

```sql
SELECT trade_id, row_hash FROM market_events_research_lake_v1
```

Plan:

- `2 | 0 | 0 | SCAN market_events_research_lake_v1 USING COVERING INDEX idx_rlake_v1_row_hash`

### #5 — 0.0003s (rows=1)

```sql
SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL
```

Plan:

- `4 | 0 | 0 | SEARCH market_events_paper_trades_s42 USING INDEX idx_s42_closed_pnl_id (status=?)`

### #6 — 0.0003s (rows=0)

```sql
SELECT * FROM market_events_trade_snapshots_s56 WHERE paper_trade_id IS NOT NULL ORDER BY id ASC
```

Plan:

- `3 | 0 | 0 | SCAN market_events_trade_snapshots_s56`

### #7 — 0.0001s (rows=0)

```sql
SELECT trade_id, paper_trade_id, validation_status, score, rule_id FROM market_events_alpha_validations_v2
```

Plan:

- `(explain failed: no such table: market_events_alpha_validations_v2; retry: no such table: market_events_alpha_validations_v2)`

### #8 — 0.0s (rows=0)

```sql
SELECT key, value FROM market_events_ops_state WHERE key LIKE 'optimizer%' OR key LIKE 'g42%' LIMIT 20
```

Plan:

- `(explain failed: no such table: market_events_ops_state; retry: no such table: market_events_ops_state)`


## Synthetic 30k benchmark

- rows: 30000
- elapsed_sec: 1.903
- under_2_min: True
- v1_estimated_sec (N+1 G31/S55): N+1 (S55+S56+G31 per trade); ~61k G31 rows ⇒ minutes–hours at 30k trades

