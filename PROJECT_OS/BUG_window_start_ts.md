# PROJECT_OS — Bug: `market_checks.window_start_ts`

_Investigation only. No fix implemented._

## Summary

`link_event_context` → `_link_polymarket_state` queries **`market_checks.window_start_ts`**, but that column **has never existed** on `market_checks`. The live schema and writer use **`checked_at`** (TEXT datetime). Window start lives on **other** tables (and can be parsed from `market_slug`).

This is a **code bug introduced in Phase E.1**, not a missing migration / dropped column on an otherwise correct table.

---

## Symptom

```
[shock-paper] cycle error: no such column: window_start_ts
```

Raised when a shock is persisted and `paper_runner` calls `link_event_context`. The exception is **uncaught** inside `_link_polymarket_state` (only `finally: trades.close()`), propagates out of `run_once`, and aborts the cycle **before** `create_pending_shock` / paper open.

Live evidence (see `GATE_ANALYSIS.md`): **12** detections, **12** matching cycle errors, **0** `paper_strategy_runs`.

---

## Actual `market_checks` schema (truth)

**File:** `schema.sql` (and live `data/trades.db`)

```sql
CREATE TABLE IF NOT EXISTS market_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    seconds_remaining REAL NOT NULL,
    strike_price REAL NOT NULL,
    btc_price REAL NOT NULL,
    yes_bid REAL,
    yes_ask REAL,
    no_bid REAL,
    no_ask REAL,
    signal TEXT CHECK (signal IN ('BUY_YES', 'BUY_NO')),
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**Live row count:** ~223,724  
**Columns present:** `id, market_slug, seconds_remaining, strike_price, btc_price, yes_bid, yes_ask, no_bid, no_ask, signal, checked_at`  
**`window_start_ts`:** **absent**

### Writer (never creates `window_start_ts`)

**Module:** `bot/database.py` → `insert_market_check`

```sql
INSERT INTO market_checks (
    market_slug, seconds_remaining, strike_price, btc_price,
    yes_bid, yes_ask, no_bid, no_ask, signal
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

Time is stored only via default / `checked_at`.

### How correct callers time-filter `market_checks`

Example: `bot/no_c_filter_shadow.py` — uses **`checked_at`**, not `window_start_ts`:

```sql
SELECT btc_price,
       abs(cast(strftime('%s', checked_at) AS integer) - ?) AS delta
FROM market_checks
ORDER BY delta ASC
LIMIT 1
```

---

## Where `window_start_ts` *should* live

| Location | Role |
|----------|------|
| `early_reversion_markets.window_start_ts` | Market registry for ER |
| `virtual_trades.window_start_ts` | Late-window / virtual trades |
| `early_reversion_*_trades.window_start_ts` | ER trade ledgers |
| `v4_shadow_observations.window_start_ts` | V4 shadow path observations |
| Bidirectional / YES_C shadow trade tables | Same pattern |
| **`market_slug` suffix** | Canonical 5m window: `btc-updown-5m-{unix_ts}` via `_slug_window_start` in `bot/collector_diagnostics.py` |

For **`market_checks`**, the intended time axis for “when was this quote taken?” is **`checked_at`**, not a window-start column. If the linker wants “which 5m market window,” it should **parse `market_slug`** or join `early_reversion_markets`, not invent a column on `market_checks`.

---

## Buggy SQL (consumer)

**File:** `bot/research/market_events/event_context_linker.py`  
**Function:** `_link_polymarket_state`

```sql
SELECT id, market_slug, window_start_ts, strike_price, btc_price,
       yes_bid, yes_ask, seconds_remaining
FROM market_checks
WHERE window_start_ts BETWEEN ? AND ?
ORDER BY id DESC
LIMIT 20
```

Then uses `row["window_start_ts"]` as `context_ts`.

**Intent (docs):** Phase E.1 `polymarket_audit.py` / `PHASE_E1_ARCHITECTURE_AUDIT.md` — link nearby Polymarket state as `POLYMARKET_STATE` context within ~30 minutes (`CONTEXT_WINDOWS_SEC["POLYMARKET_STATE"]`).

**Mismatch:** Code assumed a trade-table-shaped schema on a poll/check table.

---

## Why it is absent

1. **`market_checks` was designed without `window_start_ts` from day one** (schema + INSERT).
2. **No migration ever added that column** — git history of `schema.sql` never puts `window_start_ts` on `market_checks`.
3. **E.1 linker copied the wrong column name** from ER/virtual trade tables (where `window_start_ts` is standard).
4. **Error handling asymmetry:** `_link_agent_theses` wraps failures in `except Exception: pass`; `_link_polymarket_state` does **not** — so a schema mistake becomes a **hard cycle abort** instead of “skip context.”

This is **not** “data not populated yet.” The column cannot be populated by current writers because it does not exist.

---

## Schema vs code — which is outdated?

| Artifact | Status |
|----------|--------|
| `schema.sql` `market_checks` | **Correct / current** (no `window_start_ts`) |
| Live `trades.db` | Matches schema |
| `insert_market_check` | Matches schema |
| Other production readers (`no_c_filter_shadow`, reports) | Use `checked_at` — **correct** |
| `event_context_linker._link_polymarket_state` | **Outdated / wrong at birth** |
| E.1 unit tests (`tests/test_market_events_e1.py`) | **Never exercised** this SQL path |

**Conclusion:** Code is wrong; schema is right. Fix belongs in the linker (query `checked_at` and/or derive window from slug), **not** in adding `window_start_ts` to `market_checks` unless there is an explicit product decision to denormalize (would require migration + writer change + backfill). Prefer aligning code to existing schema.

---

## Commit that introduced the mismatch

| Field | Value |
|-------|-------|
| **Commit** | `40f2d67009500045f5c7e0aa17434f5e8092a266` (`40f2d67`) |
| **Date** | 2026-07-09 19:32:57 +0300 |
| **Subject** | Add Phase E.1 shock paper layer and D.1.1 robustness audit. |
| **File added** | `bot/research/market_events/event_context_linker.py` (includes buggy SQL from first revision) |

`git blame` on the SELECT still points entirely at **`40f2d67`**.

Later touches to the same file (`974dccd`, `5836fb8`, `32ed419`) changed connection plumbing (e.g. unified RO sqlite manager) but **did not change** the `window_start_ts` SQL.

### Schema baseline (for contrast)

| Commit | Date | Note |
|--------|------|------|
| `59a3484` | 2026-06-21 | Introduced `schema.sql` with `market_checks` **without** `window_start_ts` (and with `window_start_ts` on `virtual_trades` / `early_reversion_*`) |

So the mismatch is: **E.1 (Jul 9) assumed a column that ER schema (Jun 21) never defined on that table.**

---

## Failure chain (runtime)

```
shock detect
  → INSERT market_events
  → INSERT market_event_snapshots
  → link_event_context
       → _link_polymarket_state
            → SELECT … window_start_ts FROM market_checks
            → OperationalError: no such column: window_start_ts
  → run_once aborts
  → create_pending_shock NEVER RUNS
  → paper_strategy_runs stays 0
```

---

## Fix directions (not implemented)

Docs only — options for a later change:

1. **Minimal / correct:** Rewrite `_link_polymarket_state` to filter on `cast(strftime('%s', checked_at) AS integer)` (same pattern as NO_C filter), optionally include slug-parsed window in `context_json`.
2. **Hardening:** Wrap `_link_polymarket_state` in `except Exception` like agent linking so context failures cannot abort paper.
3. **Do not** add `window_start_ts` to `market_checks` unless product explicitly wants denormalized window on every poll row (large migration; redundant with slug).

---

## Related docs

- `GATE_ANALYSIS.md` — funnel impact (12/12 detections killed here)
- `LIVE_WRITE_PATH.md` — shock-paper write path
- `docs/research/PHASE_E1_ARCHITECTURE_AUDIT.md` — intended Polymarket context link
