# CONTEXT

**Updated:** 2026-07-28  
**Branch:** `develop-terminal`  
**Tip:** Expectancy Intelligence V1 (analytics only; no gate changes)  
**Companion:** refresh `PROJECT_OS/HANDOFF.md` every session.

---

## What this project is

Futures shock paper trading + research CLI (`bot.research.market_events`).  
Live spine: detect → paper → learn. Primary DB: `data/market_events.db`.

## Where we are (2026-07-27)

| Layer | State |
|-------|--------|
| Live feed | Restored — Bybit/Polymarket fetch OK when network available |
| SQLite | Contention fix shipped (`79986a5`); locks ~0 |
| Performance | CLI `performance` shipped; read-only on `paper_strategy_runs` |
| Trade Intelligence V1 | **Shipped** — `bot/research/market_events/trade_intelligence/`, schema v66, CLI `trade import\|list\|report\|similar` |
| Expectancy Intelligence V1 | **Shipped** — `bot/research/market_events/expectancy_intelligence/`, diagnostics only (no trading logic) |
| Research Pack 01 | **Shipped** — `trade-statistics` → `reports/research/*` (quantile buckets, n≥30 reliability, CI95, EV×log(n) playbook rank) |
| Doctor | HEALTHY (core + tradfi + dashboard typical) |
| Paper book | **0 completed / 0 open** — waiting for first real fills |

## Hard constraints

Do **not** change:

- Trading / entry / exit / gate logic
- Performance metric calculations
- Live vs research DB separation without an explicit task

## Product focus

**Current goal:** collect first real paper trades.  
**Next goal:** AI Trade Intelligence V2 (LLM plugs into existing envelope; no rewrite of V1 shapes).

## Useful entry points

| Path | Role |
|------|------|
| `PROJECT_OS/HANDOFF.md` | Session bootstrap (status, metrics, commands) |
| `PROJECT_OS/CONTEXT.md` | This file — short project context |
| `bot/research/market_events/trade_intelligence/` | TI V1 package |
| `bot/research/market_events/expectancy_intelligence/` | Expectancy / feature / counterfactual analytics |
| `bot/research/market_events/performance.py` | Paper analytics (do not alter for TI) |
| `data/market_events.db` | Live market events + paper + `ti_*` |

## Trade Intelligence V1 commands

```bash
python -m bot.research.market_events trade import --source paper
python -m bot.research.market_events trade list
python -m bot.research.market_events trade report
python -m bot.research.market_events trade similar --trade-id ID
```

## Expectancy Intelligence V1 commands (read-only analytics)

```bash
python -m bot.research.market_events expectancy-breakdown --hours 24
python -m bot.research.market_events feature-importance
# Legacy G4 validation report: feature-importance --g4-validation
python -m bot.research.market_events similar-trades BTC
python -m bot.research.market_events counterfactual --hours 24
python -m bot.research.market_events daily-intelligence
python -m bot.research.market_events dataset-audit
python -m bot.research.market_events trade-statistics
```

## Research Sync V1 (same DB on every machine)

```bash
python -m bot.research.market_events research-sync-export
python -m bot.research.market_events research-sync-import --file data/research_snapshots/research_snapshot_*.tar.gz
python -m bot.research.market_events research-sync-import --file … --activate
python -m bot.research.market_events research-sync-status
```

Bundle: `market_events.db` + `manifest.json` (SHA256, snapshot date, counts). `research-sync-status` → `SYNCED` when analytics DB matches manifest.

Closed S42 paper trades auto-write `ti_paper_knowledge` (structured memory; no gate changes).

## Local leftovers (not part of TI V1)

Uncommitted feed-restore / proxy scrub (`process_manager.py`, `venue_bybit.py`, `okx_client.py`), misc `PROJECT_OS/*` research docs, dataset provenance — ship only under their own task. Do **not** fold into TI commits. Never commit `logs/`.
