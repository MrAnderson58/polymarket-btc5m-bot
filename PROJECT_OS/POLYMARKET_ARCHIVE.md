# PROJECT_OS — Polymarket Archive

**Universe:** `polymarket_hist`  
**Status:** ARCHIVED — research branch only  
**Updated:** 2026-07-26

---

## What this is

Closed-trade research snapshots imported from Polymarket / ER–shadow tables in `trades.db` into RESEARCH `market_events_trade_snapshots_s56` as `hist:*` signal types.

| Fact (this workspace) | Value |
|-----------------------|-------|
| S56 rows with `pnl_usd` | **1786** |
| LIVE S42 | **0** |
| Lab parquet rows | **1786** |
| Identity | UNIQUE `(s40_signal_type, s40_signal_id)` — no dup keys |
| Distinct `paper_trade_id` | 803 (numeric id reuse across source tables — **not** true trade dupes) |

Provenance detail: `docs/DATASET_PROVENANCE_AUDIT.md`.

---

## Source mix (archived)

| `s40_signal_type` | ≈n | `source_table` |
|-------------------|----|----------------|
| `hist:er_v2` | 513 | `early_reversion_v2_trades` |
| `hist:er_v1` | 493 | `early_reversion_trades` |
| `hist:er_v3` | 282 | `early_reversion_v3_trades` |
| `hist:er_v25` | 221 | `early_reversion_v25_trades` |
| `hist:yes_c_shadow` | 124 | `yes_c_shadow_trades` |
| `hist:v4_shadow` | 107 | `v4_shadow_trades` |
| `hist:virtual` | 46 | `virtual_trades` |

Discarded from backfill: closed/settled rows with **NULL pnl** (4 er_v1 + 2 virtual locally).

---

## What was learned (keep as archive value)

1. `load_lab_trades` / S59–S66 on this DB were **hist-only** — classical funding/ATR/regime mostly empty.  
2. Strong “predictors” were often provenance or leakage (exit, mfe/mae, `btc_move_*`).  
3. Open-time dataset builder + leakage rejector + validator are reusable **patterns** for futures_paper.  
4. Phase 5 reports under `research/reports/` for hist remain useful as methodology fixtures.

---

## Rules for touching this universe

| Allowed | Not allowed |
|---------|-------------|
| Labeled research: `universe=polymarket_hist` | Implied default for new product work |
| Compare hist vs futures **side-by-side** with two banners | Merging hist rows into futures_paper datasets unlabeled |
| Freeze / cite 1786 parquet as archive artifact | Claiming “we have 28k Polymarket paper trades” from this DB |

---

## Separate: Polymarket live bot

The original Polymarket BTC 5m bot (`bot/main`, ER strategies, `trades.db` execution) is a **different stack** from market_events futures paper. Hard constraints for that bot remain in `.cursor/PROJECT_CONTEXT.md`.  
Archiving **hist analytics** here does not mean deleting the live Polymarket bot.

---

## Do not resume as primary unless

1. Explicit decision in `DECISIONS.md`, and  
2. Clear product goal distinct from futures paper, and  
3. All outputs labeled `universe=polymarket_hist`.
