# HANDOFF

## Date
2026-07-28

## Branch
develop-terminal

## Last Commit
476277d Research Inventory V1

## Research Inventory
See `PROJECT_OS/RESEARCH_INVENTORY_V1.md` for full analytics audit + roadmap.

## Current Status

✅ Live feed restored

✅ SQLite diagnostics + unified gate funnel (prior commits on develop-terminal)

✅ **Expectancy Intelligence V1** — analytics CLIs + `ti_paper_knowledge` on close (no gate changes)

✅ Trade Intelligence foundation

✅ Doctor HEALTHY

## Running Services

| Service | Status |
|---------|--------|
| shock-paper-core | running |
| shock-paper-tradfi | running |
| observe | stopped |
| ai | stopped |
| g3 | stopped |
| learning | stopped |
| telegram | stopped |
| dashboard | running |

## Database

`data/market_events.db`

Schema v66 (Trade Intelligence V1 `ti_*` tables)

## Current Metrics

Completed paper trades:
0

Open paper trades:
0

Recent locks:
0

Fetch OK:
working (Bybit / Polymarket OK; `watch --once` HEALTHY)

## Things NEVER to change

Trading logic

Exit logic

Performance calculations

## Current Goal

Collect first real paper trades.

## Next Goal

AI Trade Intelligence V2

## Commands

```bash
git fetch origin
git reset --hard origin/develop-terminal

python -m bot.research.market_events doctor
python -m bot.research.market_events self-test
python -m bot.research.market_events watch --once
python -m bot.research.market_events performance
python -m bot.research.market_events trade list
python -m bot.research.market_events expectancy-breakdown --hours 24
python -m bot.research.market_events feature-importance
python -m bot.research.market_events similar-trades BTC
python -m bot.research.market_events counterfactual --hours 24
python -m bot.research.market_events daily-intelligence
python -m bot.research.market_events dataset-audit
python -m bot.research.market_events trade-statistics
python -m bot.research.market_events feature-validation
python -m bot.research.market_events research-sync-status
python -m bot.research.market_events sqlite-contention-report
```

## Trade Intelligence V1 (shipped this session)

```bash
python -m bot.research.market_events trade import --source paper
python -m bot.research.market_events trade import --source csv --csv PATH
python -m bot.research.market_events trade import --source manual --symbols BTC --side LONG
python -m bot.research.market_events trade list
python -m bot.research.market_events trade report [--trade-id ID]
python -m bot.research.market_events trade similar --trade-id ID
```

Package: `bot/research/market_events/trade_intelligence/`  
Envelope: Trade, Market Snapshot, News, Telegram, Context, Outcome, AI Summary (placeholder), Tags, Notes.  
No LLM yet. Do not modify trading logic or Performance.

## Session notes

- Feed-restore edits (`process_manager.py`, `venue_bybit.py`, `okx_client.py`, `LIVE_FEED_RESTORE.md`) may still be local/uncommitted — keep out of unrelated commits unless shipping that stage.
- Refresh this file at the start/end of every session.
