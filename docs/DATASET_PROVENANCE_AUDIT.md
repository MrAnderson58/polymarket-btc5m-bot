# Dataset Provenance Audit

_generated=2026-07-25T21:51:57.024239+00:00 elapsed=0.405s_

## Executive answer

- **Local RESEARCH / lab trade count: `1786`** (not 28 322).
- Parquet `lab_dataset`: `1786`
- LIVE S42 closed: `0` · total: `0`
- Verdict: **`EXPECTED_FOR_HIST_ONLY_RESEARCH_DB`**

### Why not 28 322?

On this workspace the research universe is 1786 hist:* rows, not 28322. 28322 was cited in Phase 2 as an illustrative S56-vs-S42 mismatch pattern (audit/lab reads RESEARCH S56 including history backfill; paper-performance reads LIVE S42).

The figure **28 322** appears in `docs/TRADE_DATA_FLOW.md` as an example of `audit-trade-data` / S56 (research) ≫ S42 (live paper). It is **not** the row count in this workspace’s DBs or parquet.

## 1. Why this many trades?

S56 RESEARCH has 1786 closed snapshots with pnl_usd; 100% are history backfill (hist:*) from trades.db via backfill-history. LIVE S42 closed=0. This is NOT 28322 on this machine.

## 2. Source tables

| source_table (snapshot_json) | rows |
|---|---:|
| `early_reversion_v2_trades` | 513 |
| `early_reversion_trades` | 493 |
| `early_reversion_v3_trades` | 282 |
| `early_reversion_v25_trades` | 221 |
| `yes_c_shadow_trades` | 124 |
| `v4_shadow_trades` | 107 |
| `virtual_trades` | 46 |

## 3. Strategies / signal families included

| s40_signal_type | rows |
|---|---:|
| `hist:er_v2` | 513 |
| `hist:er_v1` | 493 |
| `hist:er_v3` | 282 |
| `hist:er_v25` | 221 |
| `hist:yes_c_shadow` | 124 |
| `hist:v4_shadow` | 107 |
| `hist:virtual` | 46 |

### strategy field inside snapshot_json

| strategy | rows |
|---|---:|
| `NO_C` | 827 |
| `YES_C` | 454 |
| `YES_B` | 318 |
| `v4_shadow` | 107 |
| `virtual` | 46 |
| `NO_B` | 15 |
| `YES_A` | 12 |
| `NO_A` | 7 |

## 4. What was discarded / never imported

Backfill requires status filter + usable PnL column. Closed/settled rows with NULL pnl are skipped (INSERT not applied).

### Empty / unused hist source tables in trades.db

_none_

### Status-eligible but not in S56

| source | eligible | with_pnl | s56 | discarded |
|---|---:|---:|---:|---:|
| `early_reversion_v2_trades` | 513 | 513 | 513 | 0 |
| `early_reversion_v3_trades` | 282 | 282 | 282 | 0 |
| `early_reversion_v25_trades` | 221 | 221 | 221 | 0 |
| `early_reversion_trades` | 497 | 493 | 493 | 4 |
| `yes_c_shadow_trades` | 124 | 124 | 124 | 0 |
| `v4_shadow_trades` | 107 | 107 | 107 | 0 |
| `bidirectional_shadow_trades` | 0 | 0 | 0 | 0 |
| `bidirectional_shadow_v12_trades` | 0 | 0 | 0 | 0 |
| `virtual_trades` | 48 | 46 | 46 | 2 |

### Discard examples

- `early_reversion_trades` id=420 status=closed pnl_usdc=None pnl_percent=None → missing_usable_pnl_or_not_upserted
- `early_reversion_trades` id=421 status=closed pnl_usdc=None pnl_percent=None → missing_usable_pnl_or_not_upserted
- `early_reversion_trades` id=422 status=closed pnl_usdc=None pnl_percent=None → missing_usable_pnl_or_not_upserted
- `early_reversion_trades` id=423 status=closed pnl_usdc=None pnl_percent=None → missing_usable_pnl_or_not_upserted
- `virtual_trades` id=16 status=settled pnl_usdc=None pnl_percent=None → missing_usable_pnl_or_not_upserted
- `virtual_trades` id=17 status=settled pnl_usdc=None pnl_percent=None → missing_usable_pnl_or_not_upserted

## 5. Duplicates?

- Duplicate `(s40_signal_type, s40_signal_id)`: **0**
- Distinct signal pairs: **1786**
- Distinct `paper_trade_id`: **803**
- `paper_trade_id` shared across strategies: **485** (max rows/pid=6)
- `paper_trade_id == s40_signal_id`: {'equal': 1786, 'total': 1786, 'pct': 100.0}

No duplicate (s40_signal_type, s40_signal_id) keys. Low distinct paper_trade_id is expected: hist backfill sets paper_trade_id = source table id, so the same integer can appear in er_v2 and er_v3 as different trades (not true duplicates).

## 6. Source overlap

- Common and expected; identity is (hist:label, id), not bare id.
- Live paper vs hist: S42 closed=0, S56 hist=1786, overlap=0

## 7. Does the count match expectation?

```json
{
  "matches_s56_pnl_rows": true,
  "matches_parquet": true,
  "matches_distinct_signal_pairs": true,
  "no_duplicate_signal_pairs": true,
  "live_s42_closed_equals_s56": false,
  "expected_lab_equals_hist_backfill_with_pnl": true,
  "verdict": "EXPECTED_FOR_HIST_ONLY_RESEARCH_DB"
}
```

## Paths

- **research_db**: `/Users/andrey/polymarket-bot/polymarket-btc5m-bot/data/market_events_research.db`
- **live_db**: `/Users/andrey/polymarket-bot/polymarket-btc5m-bot/data/market_events.db`
- **trades_db**: `data/trades.db`
- **dataset_parquet**: `/Users/andrey/polymarket-bot/polymarket-btc5m-bot/research/datasets/lab_dataset.parquet`
