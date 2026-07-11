# Phase E.5.1 — System Validation

No new product features. Validates everything built in E.1–E.5.

## CLI

```bash
python -m bot.research.market_events system-validation
python -m bot.research.market_events system-validation --read-only
python -m bot.research.market_events system-validation --json
python -m bot.research.market_events system-validation --load-events 500 --skip-load
```

## Checks

| Section | Checks |
|---------|--------|
| Concurrency | DB isolation, telegram poll singleton, AI worker separate connection |
| Database | SQLite quick_check, file size, table growth, stale AI jobs |
| Dedupe | alert dedupe keys, event dedup, AI job dedupe, digest dedupe, functional double-send |
| Restart | pending restore, open paper rows, alert log persistence across reconnect |
| Performance | insert/format/enqueue/AI process benchmark (temp DB) |
| Load test | N synthetic events on isolated temp DB — never production |

## Exit code

- `0` — no FAIL checks
- `1` — one or more FAIL

## Mac Mini

```bash
git pull origin cursor/strategy-discovery-v2
python -m bot.research.market_events market-event-migrate
python -m bot.research.market_events system-validation
pytest tests/test_market_events_e51.py -q
```

Production DB: use `--read-only` to skip mutating benchmarks (dedupe audit on live data still runs read-only queries).
