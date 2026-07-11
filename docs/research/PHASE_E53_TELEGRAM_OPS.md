# Phase E.5.3 — Telegram Operations & End-to-End Validation

Operational CLIs for Telegram integration validation before Phase F.0.

## Commands

```bash
python -m bot.research.market_events telegram-alert-test
python -m bot.research.market_events ai-test
python -m bot.research.market_events ai-test --send-telegram
python -m bot.research.market_events demo-event
python -m bot.research.market_events telegram-health
```

## telegram-alert-test

Sends a deterministic test message via `deliver_telegram` (same path as production `_send_telegram`).
Exit code `0` only when Telegram confirms delivery.

Reports: token configured, chat ID (masked), HTTP status, message_id, latency.

## ai-test

Creates a synthetic shock event, enqueues and processes one AI job with the deterministic provider.
No market polling. Use `--send-telegram` to exercise the AI research note alert path.

## demo-event

Full offline pipeline:

Synthetic shock → persist → Telegram alert → opportunity score → timeline → AI queue → analysis → AI note

No exchange requests, paper orders, or live trading.

## telegram-health

Shows bot token/chat status, delivery stats from `market_event_telegram_delivery_log`, alert/AI/retry queues.

## Schema v10

Table `market_event_telegram_delivery_log` records every send attempt:

- `status`, `latency_ms`, `http_code`, `telegram_message_id`, `attempt`, `error`

Retry policy (in `telegram_delivery.py`): exponential backoff on 429, 500, 502, 503, 504, and timeouts.

Env: `ME_ALERT_MAX_RETRIES`, `ME_ALERT_RETRY_DELAY_SEC`, `ME_ALERT_RETRY_BACKOFF_MULTIPLIER`, `ME_ALERT_RETRY_MAX_DELAY_SEC`.

## Safety

Does not modify SHOCK_A–F, R1–R5, EXIT_A–E, `bot.main`, live trading, historical replay, or paper strategy logic.
