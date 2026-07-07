# Cursor Recovery Context — polymarket-btc5m-bot

**No secrets.** Repo: `~/polymarket-btc5m-bot`, branch: `migration/clob-v2`.

## Repository architecture

```
bot/
  main.py              # Main loop + V4 daemon thread
  config.py            # Env config (DATABASE_PATH, poll intervals, feature flags)
  database.py          # SQLite schema, insert_v4_shadow_observation
  v4/shadow_trade.py   # V4 FSM + record_v4_observation_tick (always persist)
  collector_diagnostics.py  # v4-density CLI, cycle metrics tables
  ops/                 # healthcheck, snapshot, prod_control (read-only ops)
  research/
    strategy_simulator/  # Stage 3 discovery, walk-forward, split-diagnostics
    futures_agent/     # Telegram inbound, PostgreSQL agent DB
    mtf/               # HTF snapshot collector (throttled)
scripts/               # prod-*, backup-databases.sh
deploy/macos/          # launchd templates (not auto-installed)
docs/operations/       # runbooks
```

## Critical entrypoints

| CLI | Purpose |
|-----|---------|
| `python -m bot.main` | Production main loop |
| `python -m bot.research.futures_agent telegram-poll` | Singleton Telegram poller |
| `python -m bot.ops healthcheck` | Production health |
| `python -m bot.ops snapshot` | Ops report to `reports/` |
| `python -m bot.collector_diagnostics v4-density` | V4 density audit |
| `python -m bot.research.strategy_simulator split-diagnostics` | Research gates |

## DB tables (research-critical, SQLite)

- `v4_shadow_observations` — primary path data for simulator
- `v4_shadow_trades` — V4 virtual trades
- `v4_collector_cycles`, `main_collector_cycles` — ops timing metadata
- `ss_shadow_candidates`, `ss_simulation_results`, `ss_discovered_strategies`
- `bidirectional_shadow_observations`, `bidirectional_shadow_trades`
- MTF: `mtf_market_snapshots` (via research module)

PostgreSQL (`trading_ai`): `futures_agent_*` tables only.

## Collector architecture (forbidden to regress)

1. `main.py`: `_v4_collector_loop` in **daemon thread** — not gated on `_cycle()` duration
2. `MTF_POLL_INTERVAL_SEC=60` — MTF not on every `POLL_INTERVAL_SEC` tick
3. `shadow_trade.record_v4_observation_tick()` — runs **before** trade FSM early returns
4. Only writer to `v4_shadow_observations`: `insert_v4_shadow_observation` via `shadow_trade.py`

## Forbidden coupling

- Do not move V4 observations back into blocking `_cycle()` only path
- Do not run MTF collector every 2s main cycle
- Do not skip observations when V4 trade open/closed
- Strategy simulator must not import execution / place orders
- Ops modules (`bot.ops`) must stay read-only — no `execution`, no order placement

## No-look-ahead requirements

- Strategy simulator uses chronological splits (`split_markets_chronological`)
- Walk-forward: train → validation → test; no overlap
- Features from `v4_shadow_observations` paths only at `seconds_from_start` ≤ decision time
- `split-diagnostics` required before `shadow-enable`

## Observe-only boundaries

| Module | Touches CLOB orders? |
|--------|---------------------|
| V4 shadow | No |
| Strategy simulator | No |
| MTF collector | No |
| Futures agent telegram | No |
| `bot.ops` | No |
| ER v2 live | **Yes** if `LIVE_ENABLED` — out of scope for research changes |

## Testing commands

```bash
source .venv/bin/activate
python -m unittest tests.test_ops_healthcheck -v
python -m unittest tests.test_collector_diagnostics -v
python -m unittest discover -s tests -p 'test_strategy_simulator*.py' -q
```

## Deployment workflow

1. Develop on MacBook, push to `migration/clob-v2`
2. Mac Mini: `git pull`, `pip install -r requirements.txt` if needed
3. `./scripts/prod-restart.sh` or launchd kickstart
4. `python -m bot.ops healthcheck`
5. After 5m: `python -m bot.collector_diagnostics v4-density`

## Current branch / next dev task

**Branch:** `migration/clob-v2`

**Next development task:** Accumulate and validate post-fix V4 density on TEST-era markets; re-run `split-diagnostics` until funnel collapse is explained or resolved. **Do not** `shadow-enable` until gates pass.

**Ops pack added:** `bot.ops`, `scripts/prod-*`, `deploy/macos/`, runbooks in `docs/operations/`.

## Singleton processes

- Exactly one `python -m bot.main`
- Exactly one `telegram-poll`
- Use `./scripts/prod-status.sh` before start
