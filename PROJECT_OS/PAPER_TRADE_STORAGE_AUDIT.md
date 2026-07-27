# Paper trade storage audit (Phase 1)

**Database:** `data/market_events.db` (live shock-paper path)  
**Audit date:** 2026-07-27

## Primary table: `paper_strategy_runs`

Shock paper (E.1) writes one row per **(event, reversal variant, exit variant)** when a reversal confirms and paper entries open. Closes update the same row.

| Field | Role |
|-------|------|
| `entry_ts`, `entry_price` | Entry time and fill price |
| `exit_ts`, `exit_price`, `exit_reason` | Set on close (`paper_runner._process_open_positions`) |
| `gross_return` | Position return **in percent** (includes partial-exit blend in `paper_execution._close`) |
| `net_return` | `gross_return` minus round-trip fee+slippage (`net_return()` in `paper_execution.py`) |
| `fee_bps`, `slippage_bps` | Per-run defaults (typically **10 + 10** bps each leg) |
| `duration_seconds` | `exit_ts - entry_ts` |
| `mfe`, `mae` | Max favorable / adverse excursion (% ) |
| `strategy_name` | e.g. `REVERSAL_R1_EXIT_A` |
| `reversal_variant`, `exit_variant` | `R1`–`R5`, `EXIT_A`–`EXIT_E` |
| `eligibility` | 1 when paper entry created |

**Not stored:** USD notional, leverage, position size in contracts. Returns are **percentage only**.

**Partial exits:** Supported in memory (`remaining_frac`, `partial_taken` in `PaperPosition`); closed run stores a **single** blended `gross_return` — no separate leg rows.

**Leverage:** None in shock paper schema.

## Secondary tables (not shock E.1 runner)

| Table | Purpose |
|-------|---------|
| `market_events_paper_trades_s42` | AI / S42 paper book (`pnl_usd`, `capital_usd`, `leverage`) |
| `market_events_mtf_paper_runs` | MTF research |
| Research DB snapshots (S56+) | Post-close analytics |

Performance analytics for **“shock paper $100/trade”** should use **`paper_strategy_runs`** unless explicitly scoped to S42.

## PnL semantics for analytics

- Assume **$100 notional** per closed run when converting to USD:  
  `pnl_usd = NOTIONAL_USD * net_return / 100`
- Default notional: `ME_PAPER_NOTIONAL_USD=100`
- Equity curve default bankroll: `ME_PAPER_STARTING_EQUITY=1000` (configurable; independent of per-trade size)

## Live DB snapshot (this machine)

| Metric | Value |
|--------|-------|
| `paper_strategy_runs` total | 0 |
| Closed runs | 0 |
| `market_events` | 12 (detections; no completed paper path yet) |

Analytics module must handle **zero trades** gracefully and will populate once reversals confirm and exits close.

## Code references

- Open: `paper_runner.py` → `INSERT OR IGNORE INTO paper_strategy_runs`
- Close: `UPDATE paper_strategy_runs SET exit_ts, net_return, ...`
- Exit logic: `paper_execution.py` (`EXIT_POLICIES`, partial TP, BE, trail)
