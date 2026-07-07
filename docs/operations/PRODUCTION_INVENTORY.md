# Production Inventory — Mac Mini

Repository: `~/polymarket-btc5m-bot`  
Branch (target): `migration/clob-v2`  
Python: `~/polymarket-btc5m-bot/.venv/bin/python`

## Singleton processes (never duplicate)

| Process | Command | Singleton mechanism |
|---------|---------|-------------------|
| Main bot | `python -m bot.main` | No file lock; **must** run alone (SQLite writer, in-process V4 thread) |
| Telegram poller | `python -m bot.research.futures_agent telegram-poll` | `fcntl.flock` on `data/futures_agent_telegram_poll.lock` |

**Not separate processes:** V4 collector runs as daemon thread inside `bot.main` when `ENABLE_V4_SHADOW=true`.

## Entrypoints

| Role | Command | Module |
|------|---------|--------|
| Main production loop | `python -m bot.main` | `bot/main.py` → `run()` |
| Futures Telegram inbound | `python -m bot.research.futures_agent telegram-poll` | observe-only signal ingestion |
| Health check | `python -m bot.ops healthcheck` | read-only ops |
| Ops snapshot | `python -m bot.ops snapshot` | read-only report |
| V4 density audit | `python -m bot.collector_diagnostics v4-density` | research DB audit |
| Telegram diagnose | `python -m bot.research.futures_agent telegram-diagnose` | inbound diagnostics |
| Strategy split diagnostics | `python -m bot.research.strategy_simulator split-diagnostics` | walk-forward gates |

## How production is started today

**Observed in repo:** no launchd plist was committed before this recovery pack. Production on Mac Mini is likely started via:

- manual terminal: `python -m bot.main` + `telegram-poll` in separate sessions, **or**
- ad-hoc `nohup` / `screen` / `tmux`

**Linux reference only:** `deploy/futures-agent-telegram.service` (systemd) — not used on macOS.

**Recovery pack adds (templates only, not auto-installed):**

- `deploy/macos/com.polymarket.bot-main.plist`
- `deploy/macos/com.polymarket.futures-agent-telegram.plist`
- `scripts/prod-{status,start,stop,restart}.sh`

## Databases

| Store | Resolved path / name | Used by |
|-------|----------------------|---------|
| SQLite (primary bot) | `DATABASE_PATH` → default `data/trades.db` | `bot.main`, V4 shadow, strategy simulator, MTF snapshots |
| PostgreSQL `trading_ai` | `FUTURES_AGENT_DATABASE_URL` (e.g. `postgresql:///trading_ai`) | futures_agent tables only |
| SQLite fallback agent | `FUTURES_AGENT_SQLITE_PATH` → default `data/futures_agent.db` | dev/tests if PG not set |

**Resolution:** `bot/config.py` (`DATABASE_PATH`), `bot/research/futures_agent/env_bootstrap.py` (agent DB).

## Environment variables (names only)

### Main bot (`bot/config.py`)

`CLOB_HOST`, `CHAIN_ID`, `GAMMA_API`, `BINANCE_API`, `BTC_SYMBOL`, `BTC_PRICE_CACHE_TTL_SEC`, `POLL_INTERVAL_SEC`, `STRATEGY_WINDOW_SEC`, `STRIKE_THRESHOLD_USD`, `TRADE_SIZE_USDC`, `DATABASE_PATH`, `EARLY_REVERSION_*`, `ENABLED_STRATEGIES*`, `ENABLE_V1`, `ENABLE_V2`, `ENABLE_V25`, `ENABLE_V3`, `ENABLE_V4_SHADOW`, `ENABLE_YES_C_SHADOW`, `ENABLE_NO_C_FILTER_SHADOW`, `ENABLE_LATE_WINDOW`, `ENABLE_TRAILING_STOP`, `EXIT_MODE`, `TRAILING_*`, `ER_*`, `V4_POLL_INTERVAL_SEC`, `MTF_POLL_INTERVAL_SEC`, `V4_OBSERVE_SECONDS`, `V4_MIN_*`, `V4_PULLBACK`, `TRADING_MODE`, `POLY_PRIVATE_KEY`, `POLY_PROXY_WALLET`, `POLY_SIGNATURE_TYPE`, `MAX_OPEN_POSITIONS`, `MAX_DAILY_LOSS_USDC`, `LIVE_*`, `PORTFOLIO_*`

### Production collector config (validated)

`POLL_INTERVAL_SEC=2`, `V4_POLL_INTERVAL_SEC=1`, `MTF_POLL_INTERVAL_SEC=60`, `ENABLE_V4_SHADOW=true`

### Futures agent

`FUTURES_AGENT_DATABASE_URL`, `FUTURES_AGENT_SQLITE_PATH`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_AGENT_CHAT_ID`, `TELEGRAM_CHAT_ID`, `TELEGRAM_AGENT_ALLOWED_CHAT_IDS`, `FUTURES_AGENT_INCLUDE_ETH`

### Futures research source (read-only)

`FUTURES_SOURCE_DATABASE_URL`, `TELEGRAM_DATABASE_URL`, `DATABASE_URL`, `FUTURES_SOURCE_BACKEND`, `FUTURES_REQUIRE_POSTGRES`, `FUTURES_RESEARCH_DATABASE_PATH`

## Log locations

| Source | Path |
|--------|------|
| Manual/script start (main) | `logs/bot-main.log` |
| Manual/script start (telegram) | `logs/futures-agent-telegram.log` |
| launchd (if installed) | `logs/launchd-bot-main.log`, `logs/launchd-futures-agent-telegram.log` |
| Main bot default | stdout (if run in foreground terminal) |

## Lock / state files

| File | Purpose |
|------|---------|
| `data/futures_agent_telegram_poll.lock` | Singleton telegram poller (`fcntl`) |
| `data/futures_agent_telegram_offset.json` | Telegram `getUpdates` offset |
| `data/futures_agent_telegram_last.json` | Last processed message metadata |

**No PID files** for main bot. Use `scripts/prod-status.sh` or `python -m bot.ops healthcheck`.

## Startup dependencies

1. Network (Polymarket Gamma/CLOB, Binance BTC price, Telegram API)
2. `.env` at repo root (not in git)
3. SQLite `data/trades.db` (created by `init_db` on first run)
4. PostgreSQL running locally for futures_agent when `FUTURES_AGENT_DATABASE_URL` is postgres
5. Python `.venv` with `requirements.txt` installed

## Observe-only vs execution-capable

| Component | Mode |
|-----------|------|
| `bot.main` with `TRADING_MODE=paper` | Virtual/paper trades; ER v2 may be live-enabled via flags — **check `LIVE_ENABLED`, `TRADING_MODE` before reboot** |
| V4 shadow | Observe-only (no CLOB orders) |
| YES_C / NO_C filter shadow | Observe-only |
| Bidirectional shadow v1.1/v1.2 | Observe-only |
| MTF collector | Observe-only |
| Strategy simulator | Offline research CLI |
| Futures agent telegram-poll | Observe-only ingestion |
| `bot.clob_healthcheck` | Signs test order only when explicitly run |

**Current research stance:** NO-GO for shadow-enable / live expansion until TEST-era dense V4 data and funnel validation pass.

## Network dependencies

- `gamma-api.polymarket.com` — market discovery
- `clob.polymarket.com` — quotes (read-only for research paths)
- `api.binance.com` — BTC spot
- `api.telegram.org` — futures agent inbound

## Critical research tables (SQLite)

`v4_shadow_observations`, `v4_shadow_trades`, `v4_collector_cycles`, `main_collector_cycles`, `bidirectional_shadow_*`, `ss_shadow_candidates`, `ss_simulation_results`, `ss_discovered_strategies`, MTF snapshot tables

## PostgreSQL tables (futures agent)

`futures_agent_inputs`, `futures_agent_signals`, `futures_agent_targets`, `futures_agent_market_snapshots`, `futures_agent_btc_context`, `futures_agent_relative_strength`

## V4 collector architecture (do not revert)

- V4 runs in **dedicated daemon thread** inside `bot.main`
- MTF collection throttled by `MTF_POLL_INTERVAL_SEC` (not every main cycle)
- Observations persisted **always**, independent of V4 trade FSM state
- Validated density: ~89–113 obs/market, median gap ~3s on completed markets

## Quick health commands

```bash
cd ~/polymarket-btc5m-bot && source .venv/bin/activate
python -m bot.ops healthcheck
./scripts/prod-status.sh
python -m bot.collector_diagnostics v4-density --last-markets 10
python -m bot.research.futures_agent telegram-diagnose
```
