# Live market feed restore — proxy diagnosis

**Date:** 2026-07-27  
**Scope:** Market data pipeline only (no strategy / exit changes).

## Phase 1 — Why all fetches failed

| Item | Finding |
|------|---------|
| Proxy URL | `http://127.0.0.1:65470` |
| Env vars on shock-paper PIDs (32397/32402) | `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and lowercase twins; `NO_PROXY=127.0.0.1,::1,localhost` |
| Configured in project `.env`? | **No** (only `POLY_PROXY_WALLET`, unrelated) |
| Configured in code? | **No** hard-coded proxy |
| How injected | `process_manager.start_service` used `os.environ.copy()` — inherited Cursor/IDE MITM proxy when services were started from that environment |
| Bybit client using it? | **Yes** — `requests.get` trusts env proxies by default (`venue_bybit.py`) |
| Proxy should exist? | **No** — nothing listening on `:65470` (`Connection refused`) |
| Direct Bybit (no proxy) | **Works** — HTTP 200, live BTC price |

Older processes (ai-worker / dashboard, started Jul 13) had **no** proxy env and were unaffected.

## Phase 2 — Fix applied

1. **Restart** `shock-paper-core` / `shock-paper-tradfi` with proxy env scrubbed (new PIDs).
2. **`process_manager._scrub_proxy_env`** — strip proxy keys on every service start so IDE env cannot poison exchange polls again.
3. **`BybitMarketClient`** — `requests.Session(trust_env=False)` for direct exchange calls.
4. **OKX fallback** — same `trust_env=False` session helper.

No new networking libraries. No trading / exit / strategy changes.

## Phase 3 — Recovery evidence (post-restart)

New PIDs: core **3459**, tradfi **3486** — **no PROXY env**.

```
[shock-paper] heartbeat
universe=core
uptime=00:04:28
cycles=42
fetch_ok=420
fetch_failed=0
events_detected=0
pending_reversals=0
paper_runs_open=0
```

- Heartbeat age: **1s**, writer `shock-paper`, status `ok`
- BTC Bybit live: **~64537**
- `ProxyError` since restart: **0**

## Phase 4 — Paper pipeline (natural shock)

After ~3–4 minutes of healthy polling: still **0** new events / pending / paper rows.

Detectors reject every symbol on quiet tape (`below_return_threshold` / `relative_move_failed`).  
**Feed path is restored;** opening a paper row requires the next natural shock (thresholds unchanged by design).

## Phase 5 — Snapshot SQL

| Metric | Value |
|--------|------:|
| Open `paper_strategy_runs` | 0 |
| Closed `paper_strategy_runs` | 0 |
| Pending shocks | 0 |
| `market_events` | 12 (historical, still `SHOCK_DETECTED`) |
