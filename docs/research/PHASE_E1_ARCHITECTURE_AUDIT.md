# Phase E.1 Architecture Audit

**Date:** 2026-07-09  
**Branch:** `cursor/strategy-discovery-v2`  
**Scope:** Unified non-destructive shock-reversal paper layer  
**Verdict:** No blocking conflicts — proceed with additive `market_events` module on isolated SQLite DB.

---

## A. Existing Polymarket

| Component | Path | Backend | Notes |
|-----------|------|---------|-------|
| Main loop | `bot/main.py` | SQLite `data/trades.db` | Singleton writer; 2s poll + optional V4 thread |
| Market scanner | `bot/market_scanner.py` | — | Gamma + CLOB best bid/ask |
| BTC/strike | `bot/btc_price.py` | — | Binance 5m candle open |
| Paper trader | `bot/paper_trader.py` | `virtual_trades` | `ENABLE_LATE_WINDOW=false` default |
| ER v2 (production) | `bot/early_reversion_v2.py` | `early_reversion_v2_trades` | `ENABLE_V2=true`, `TRADING_MODE=paper` |
| V4 shadow | `bot/v4/shadow_trade.py` | `v4_shadow_observations` | Dense tick history for research |
| Settlement | `bot/paper_trader.py`, `bot/recovery.py` | — | BTC vs strike; startup recovery |
| Execution | `bot/execution.py` | — | Routes paper/dry_run/live |

**Historical reuse:** `v4_shadow_observations`, `market_checks`, `early_reversion_v2_trades`, MTF snapshots.

**Phase E constraint:** Do not start `bot.main` from E.1; link Polymarket state via read-only `market_checks` references.

---

## B. Existing futures_agent

| Table group | Purpose | Write path | Read-only? |
|-------------|---------|------------|------------|
| `futures_agent_trader_posts/theses/levels` | Stage 3 corpus | `ingest-research`, `thesis-extract` | No |
| `futures_agent_research_signal_outcomes/events/markouts` | D.1 outcomes | `outcome-build` | No (1326 prod rows — do not rebuild) |
| `futures_agent_research_market_data_cache` | Binance 1m cache | outcome-build | Shared cache — preserve |
| `futures_agent_thesis_outcomes` | Phase C | `evaluate-theses` | Separate from D.1 |

**Source DB (PostgreSQL `trading_ai`, read-only):** `telegram_messages` (~370k), `news` (sparse), `source_ratings` (empty).

**Phase E constraint:** Context linker uses SELECT + reference IDs only; no copies of full thesis text unless in `context_json` summary.

---

## C. Market collectors

| Source | Present | In bot.main | E.1 use |
|--------|---------|-------------|---------|
| Binance REST spot/futures | Yes | BTC price only | **Reuse** for E.1 perp polling |
| Bybit | No | — | Future E.2 |
| ccxt | No | — | — |
| Websocket | No | — | E.1 uses REST poll |
| Order book depth | No (CLOB top-of-book only) | Polymarket | Optional future |
| OI/funding | Funding partial (research CLI) | No | E.1 snapshots funding field when available |

---

## D. Database map

| Table / store | Backend | Purpose | Actively written | Historical |
|---------------|---------|---------|------------------|------------|
| `data/trades.db` | SQLite | Polymarket bot | When `bot.main` runs | Yes |
| `data/futures_agent.db` or PG agent | PG/SQLite | futures_agent | Research CLI | Yes |
| PostgreSQL `trading_ai` | PG | Telegram source | External collector | Read-only |
| **`data/market_events.db`** | SQLite | **Phase E.1 (new)** | `shock-paper-run` | New |

### Phase E.1 tables (additive, isolated)

| Table | Purpose |
|-------|---------|
| `market_events` | Parent shock events (deduped) |
| `market_event_snapshots` | Price path around event |
| `paper_strategy_runs` | Parallel reversal × exit policy runs |
| `market_event_context` | Links to thesis/Telegram/Polymarket |
| `market_events_universe_log` | Versioned symbol universe |
| `market_events_runner_state` | Resume metadata |

---

## Reuse plan

1. **Do not mutate** Phase C/D tables or `trades.db` from E.1 writers.
2. **Read** `futures_agent_trader_*` and `market_checks` for context linking.
3. **Read** D.1 outcomes for future causal analysis (not E.1 entry decisions).
4. **Write** only to `data/market_events.db`.
5. **Binance futures REST** for live shock detection (same patterns as `market_provider.py`).

---

## Shock frequency estimates (assumptions)

With default thresholds (1.5% / 30s, 2% / 60s, 3% / 180s) on 10 core perps, polling every 1s:

| Target | Assumed rate | Calendar time |
|--------|--------------|---------------|
| 20 shocks | 2–8/day in volatile regime | **3–10 days** |
| 50 shocks | same | **1–3 weeks** |
| 100 shocks | same | **2–6 weeks** |

Low-volatility regimes may take longer. Run `shock-event-report --days 7` after first week to calibrate observed frequency.

---

## Polymarket paper resume

```bash
TRADING_MODE=paper ENABLE_V2=true ENABLE_V4_SHADOW=true python -m bot.main
```

Phase E `shock-paper-run` uses a **separate DB** and can run concurrently without writer conflict.

---

## Conflict check: PASS

No requirement to modify `parser.py`, `bot.main`, live execution, or Phase C/D schema. Phase E.1 implemented as isolated module.
