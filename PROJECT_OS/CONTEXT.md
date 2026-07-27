# CONTEXT

**Updated:** 2026-07-27  
**Branch:** `develop-terminal`  
**Companion:** always refresh `PROJECT_OS/HANDOFF.md` every session.

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
| Trade Intelligence V1 | Knowledge layer foundation (`ti_*`, schema v66) |
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
| `bot/research/market_events/trade_intelligence/` | TI V1 package |
| `bot/research/market_events/performance.py` | Paper analytics (do not alter for TI) |
| `data/market_events.db` | Live market events + paper + `ti_*` |

## Out of scope leftovers (do not mix into TI commits)

Local feed-restore / proxy scrub and misc `PROJECT_OS/*` research docs may still be uncommitted. Ship them only under their own task.
