# PROJECT_OS — Patch: `window_start_ts` → `checked_at`

Minimal one-file fix for `BUG_window_start_ts.md`. No schema / writer / strategy changes.

## Changed file

`bot/research/market_events/event_context_linker.py` — function `_link_polymarket_state` only.

## Changed lines (before → after)

### Before

```sql
SELECT id, market_slug, window_start_ts, strike_price, btc_price,
       yes_bid, yes_ask, seconds_remaining
FROM market_checks
WHERE window_start_ts BETWEEN ? AND ?
ORDER BY id DESC
LIMIT 20
```

```python
context_ts=int(row["window_start_ts"]),
```

### After

```sql
SELECT id, market_slug,
       cast(strftime('%s', checked_at) AS integer) AS checked_ts,
       strike_price, btc_price,
       yes_bid, yes_ask, seconds_remaining
FROM market_checks
WHERE cast(strftime('%s', checked_at) AS integer) BETWEEN ? AND ?
ORDER BY id DESC
LIMIT 20
```

```python
context_ts=int(row["checked_ts"]),
```

## What was not changed

- `schema.sql` / `trades.db` schema  
- `insert_market_check` / any writers  
- `paper_runner` / pending / reversal / detector logic  

## Verification result

| Check | Result |
|-------|--------|
| **No SQL error** | **PASS** — query against live `data/trades.db` returned 20 rows |
| **Context linking succeeds** | **PASS** — `link_event_context(...)` linked **20** `POLYMARKET_STATE` rows (temp ME DB + live trades RO) |
| **Pending shock can be created** | **PASS** — `create_pending_shock` → `market_events_pending_shocks.phase=MONITORING_REVERSAL`, event phase advanced |
| **`paper_strategy_runs` can increase after detect path** | **PASS** (path proof) — same SQL as `_open_paper_runs` inserted **5** rows (`EXIT_A`…`EXIT_E`); count 0 → 5 on temp DB |
| Live `paper_strategy_runs` | Still **0** — no new natural detection since restart (last event still ~40h ago; quiet market) |
| Live runners reloaded | **PASS** — restarted `shock-paper-core` PID **72691**, `shock-paper-tradfi` PID **72748** |
| Post-restart logs (~65s) | **PASS** — heartbeat present; **no** new `window_start_ts` / `cycle error` lines |

### Path-proof numbers (isolated temp `market_events.db`)

```
SQL_OK True rows=20
link_event_context linked=20
pending_created True phase=MONITORING_REVERSAL
paper_before=0 paper_after=5
VERIFICATION_PASS True
```

### Live implication

With the patch loaded, the next shock that persists will no longer abort at context-link; `create_pending_shock` and subsequent confirmation → `paper_strategy_runs` INSERT can run. Live paper count will rise when detectors fire (not on every quiet poll).

## Related

- `BUG_window_start_ts.md` — root cause  
- `GATE_ANALYSIS.md` — why paper stayed at 0  
