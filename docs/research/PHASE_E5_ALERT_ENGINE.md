# Phase E.5 — Telegram Alert Engine & Research Dashboard

## Overview

Builds on E.3.3 alerts without modifying SHOCK_A–F, R1–R5, EXIT_A–E, parsers, or live trading.

## Features

| Feature | Module | Mode |
|---------|--------|------|
| Unified alert format (RU/EN) | `alert_engine/format_v2.py` | PAPER ONLINE |
| Telegram heartbeat (30m) | `alert_engine/heartbeat.py` | SHADOW |
| Daily digest | `alert_engine/daily_digest.py` | SHADOW |
| Weekly report | `alert_engine/weekly_report.py` | SHADOW |
| Opportunity score 0–100 | `alert_engine/opportunity_score.py` | SHADOW (no paper impact) |
| AI vs reality comparison | `alert_engine/ai_comparison.py` | SHADOW |
| Event timeline | `alert_engine/timeline.py` | SHADOW |
| Read-only JSON API | `alert_engine/dashboard_api.py` | read-only |

## Schema v9

- `market_events_opportunity_scores`
- `market_events_ai_comparisons`
- `market_events_digest_log`
- `market_events_timeline_cache`
- `market_events_scheduler_state`

## CLI

```bash
python -m bot.research.market_events market-event-migrate
python -m bot.research.market_events market-heartbeat-preview
python -m bot.research.market_events market-daily-digest
python -m bot.research.market_events market-weekly-report
python -m bot.research.market_events market-timeline-report --event-id 123
python -m bot.research.market_events market-opportunity-report
python -m bot.research.market_events market-ai-comparison-report
python -m bot.research.market_events dashboard-api-serve --port 8765
```

## API Endpoints

- `GET /events` — recent shocks
- `GET /paper` — paper strategy runs
- `GET /alerts` — alert log
- `GET /daily` — daily digest JSON
- `GET /weekly` — weekly report JSON
- `GET /stats` — system counters
- `GET /timeline/{event_id}` — event chain

## Env

```
ME_TELEGRAM_ALERTS_ENABLED=true
ME_ALERT_LOCALE=ru|en
ME_TELEGRAM_HEARTBEAT_ENABLED=true
ME_TELEGRAM_HEARTBEAT_SEC=1800
ME_DAILY_DIGEST_ENABLED=true
ME_DAILY_DIGEST_HOUR_UTC=21
ME_WEEKLY_DIGEST_ENABLED=true
ME_DASHBOARD_API_PORT=8765
```

## Mac Mini

```bash
git pull origin cursor/strategy-discovery-v2
python -m bot.research.market_events market-event-migrate
pytest tests/test_market_events_e50.py -q
python -m bot.research.market_events market-daily-digest
# Optional: dashboard-api-serve --port 8765
```
