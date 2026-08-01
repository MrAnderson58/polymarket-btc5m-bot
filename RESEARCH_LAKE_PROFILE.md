# RESEARCH_LAKE_PROFILE

Research Lake Builder V2 — SQL profile for streaming builds.

## Summary

- builder_version: `v2-streaming`
- mode: `full`
- rows_seen / inserted / updated / skipped: 19160 / 19110 / 0 / 50
- batch_size: 1000
- elapsed_sec (wall): 15.79
- n_queries profiled: 52
- total_sql_sec: 1.8194
- n_slow (>=1s): 1
- no_select_in_trade_loop: True

## Before / After

| Metric | V1 (N+1 per trade) | V2 (streaming + preload) |
|---|---|---|
| Join strategy | SELECT S55/S56/G31/alpha inside trade loop | Preload dicts once; O(1)/O(log n) lookup |
| S42 load | Full table / unbounded | `WHERE id > last_id LIMIT 1000` batches |
| Inserts | Single end commit (or per-row) | Upsert + commit every 1000 rows |
| Indexes | Missing on `s55.paper_trade_id` etc. | Auto `CREATE INDEX IF NOT EXISTS` on JOIN keys |
| Wall time (this run) | (N× queries → minutes in `sqlite3_step`) | **15.79s** |

Target: 30k CLOSED trades under 2 minutes on Apple Silicon.

## Indexes added / ensured

- `idx_s55_paper_trade_id`
- `idx_s56_paper_trade_id`
- `idx_s42_status_id`
- `idx_s42_closed_pnl_id`
- `idx_g31_symbol_created`
- `idx_rlake_v1_row_hash`

## Top 20 slow SQL

1. **1.119s** rows=63420 params=``

```sql
SELECT id, symbol, direction, market_score, confidence, created_at FROM market_candidate_g31
```

2. **0.403s** rows=19110 params=``

```sql
-- materialize_closed_from_s40_reviews
```

3. **0.0108s** rows=1000 params=`(10000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

4. **0.0104s** rows=1000 params=`(18000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

5. **0.0103s** rows=1000 params=`(13000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

6. **0.0101s** rows=1000 params=`(16000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

7. **0.01s** rows=1000 params=`(14000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

8. **0.0099s** rows=1000 params=`(7000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

9. **0.0098s** rows=1000 params=`(12000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

10. **0.0098s** rows=1000 params=`(6000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

11. **0.0097s** rows=1000 params=`(15000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

12. **0.0094s** rows=1000 params=`(11000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

13. **0.0093s** rows=1000 params=`(9000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

14. **0.0091s** rows=1000 params=`(17000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

15. **0.0088s** rows=1000 params=`(8000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

16. **0.0087s** rows=1000 params=`(0, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

17. **0.0082s** rows=1000 params=`(5000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

18. **0.0079s** rows=1000 params=`(4000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

19. **0.0078s** rows=1000 params=`(1000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```

20. **0.0077s** rows=1000 params=`(3000, 1000)`

```sql
SELECT * FROM market_events_paper_trades_s42 WHERE status = 'CLOSED' AND pnl_pct IS NOT NULL AND id > ? ORDER BY id ASC LIMIT ?
```


## EXPLAIN QUERY PLAN (slow >= 1s, else top wall-time)

### #1 — 1.119s (rows=63420)

```sql
SELECT id, symbol, direction, market_score, confidence, created_at FROM market_candidate_g31
```

Plan:

- `2 | 0 | 0 | SCAN market_candidate_g31`

