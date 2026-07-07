# ChatGPT Recovery Context — polymarket-btc5m-bot

**No secrets in this file.** Load `.env` only on the machine.

## Project purpose

Research and paper-trading bot for Polymarket BTC 5-minute Up/Down markets. Collects high-frequency observation data, runs shadow strategies, and builds toward validated automated research — **not** production live trading yet.

## Current architecture (production Mac Mini)

| Process | Command |
|---------|---------|
| Main bot | `python -m bot.main` |
| Futures Telegram agent | `python -m bot.research.futures_agent telegram-poll` |

- **SQLite** `data/trades.db` — bot state, `v4_shadow_observations`, strategy simulator tables
- **PostgreSQL** `trading_ai` — futures agent only (`FUTURES_AGENT_DATABASE_URL`)

Branch: `migration/clob-v2`  
Venv: `~/polymarket-btc5m-bot/.venv`

## Active modules

- **ER v2** — primary enabled live strategy path (`ENABLE_V2=true`, typically `NO_C`)
- **V4 shadow** — virtual trend/pullback strategy; **observe-only** (`ENABLE_V4_SHADOW=true`)
- **V4 collector** — daemon thread inside main bot, 1 Hz observations
- **MTF collector** — HTF snapshots every 60s (`MTF_POLL_INTERVAL_SEC=60`)
- **Bidirectional shadow** v1.1/v1.2 — observe-only
- **Strategy simulator** — offline discovery, walk-forward, split-diagnostics
- **Futures agent** — Telegram signal ingestion to PostgreSQL

## Collector config (validated — do not revert)

```
POLL_INTERVAL_SEC=2
V4_POLL_INTERVAL_SEC=1
MTF_POLL_INTERVAL_SEC=60
ENABLE_V4_SHADOW=true
```

## V4 density incident (resolved)

**Broken era:** ~27–28 obs/market, ~10–11s median gap  
**Root cause:** V4 collector blocked by heavy main `_cycle()` (MTF + shadows); effective poll ~10s  
**Fix:** V4 decoupled to daemon thread; MTF throttled; observations always persisted regardless of trade FSM  
**Current:** ~89–113 obs/market, ~3s median gap on completed markets

## Strategy simulator status

- Stage 3 complete: discovery, walk-forward, bootstrap, cost model, split-diagnostics
- **NO-GO** for `shadow-enable` / live shadow until:
  - TEST-era **dense** V4 data accumulates post-fix
  - Opportunity funnel `all_conditions` stops collapsing on TEST split
- `shadow-enable` and `--export-shadow` gated on `split-diagnostics` (need `--force` to override)

## Safety boundaries

- **NO-GO** for real trading expansion
- Do not enable shadow candidates without diagnostics pass
- Do not revert V4 collector architecture
- Do not increase API load without rate-limit review
- `TRADING_MODE` / `LIVE_ENABLED` — verify before any execution discussion

## Next research milestone

1. Accumulate 2–4 weeks of dense post-fix V4 data on TEST-era markets
2. Re-run `split-diagnostics` — confirm density continuity across train/val/test
3. Re-evaluate top strategies' TEST funnel and rolling OOS
4. Only then consider `shadow-enable` for 1–2 finalists

## Health commands

```bash
cd ~/polymarket-btc5m-bot && source .venv/bin/activate
python -m bot.ops healthcheck
./scripts/prod-status.sh
python -m bot.collector_diagnostics v4-density --last-markets 10
python -m bot.research.strategy_simulator split-diagnostics --top 20 --no-progress
python -m bot.research.futures_agent telegram-diagnose
python -m bot.ops snapshot
```

## Recovery docs

- `docs/operations/PRODUCTION_INVENTORY.md`
- `docs/operations/REBOOT_RUNBOOK.md`
- `docs/operations/BLACKOUT_RECOVERY.md`
- `docs/operations/BACKUP_RESTORE.md`

## Operational scripts

```bash
./scripts/prod-{status,start,stop,restart}.sh
./scripts/backup-databases.sh
```
