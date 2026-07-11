# Phase E.4 — Historical Shock/Reversal Replay + Context Intelligence

## Goal

Test the core hypothesis on **existing data first** without touching live trading or production `market_events`:

```
sharp impulse → continuation/exhaustion → reversal confirmation → countertrend entry
→ small TP → stop to BE → partial close → trailing runner
```

## Reuse from E.3.1 Counterfactual Engine

| Component | Reused module | E.4 usage |
|-----------|---------------|-----------|
| Episode scan | `counterfactual_reversal._find_shock_episodes` | Profile shadow shocks in replay |
| Forward returns | `counterfactual_reversal._compute_metrics`, `_signed_move` | Path metrics at fixed horizons |
| Reclaim logic | `counterfactual_reversal` metrics | Reclaim % embedded in path raw_json |
| Splits pattern | `strategy_simulator/splits.py` concept | `historical_replay/splits.py` on event timestamps |
| Detectors | `shock_detector.py`, `config.SHOCK_THRESHOLDS` | SHOCK_A–E replay |
| Shadow F | `shock_f_v2_shadow.py` | SHOCK_F_v2 replay |
| Profiles | `shock_profiles.py` | Asset-class profile thresholds |
| Candles | `futures_agent/historical_candles.py` | Binance futures backfill |
| Context | `event_context_linker.py` pattern | No-lookahead replay context linker |
| Telegram | `telegram_inbound_bridge.py`, `signal_level_extract.py` | Multi-intent extraction |

## API History Limits (do not invent)

| Source | Instrument | Typical depth | Notes |
|--------|------------|---------------|-------|
| Binance futures | Crypto USDT-M | Years of 1m klines | `BinanceCandleProvider` paginates |
| Bybit | Tokenized TradFi | Varies by symbol; many lack long kline history | Backfill records actual depth per checkpoint |
| Live observations | TradFi | Hours–days currently (~126k ticks) | Coverage audit reports **span**, not tick count |

## Additive Schema (v8 market_events, stage7 futures_agent)

**market_events (SQLite research DB):**
- `market_events_historical_candles` — isolated candle store
- `market_events_candle_backfill_checkpoints` — idempotent resume
- `market_events_replay_*` — runs, splits, shocks, paths, strategies, context, AI critic

**futures_agent:**
- `futures_agent_post_multi_intent` — multi-label extractions per post

Production `market_events` table is **never written** by replay.

## CLI Commands

| Command | Task | Mode |
|---------|------|------|
| `historical-replay-coverage` | A | read-only audit |
| `historical-candle-backfill` | B | manual; not auto-started |
| `historical-candle-coverage` | B | read-only |
| `historical-shock-replay` | C/D | HISTORICAL REPLAY |
| `historical-shock-report` | J | report |
| `historical-reversal-report` | J | report |
| `historical-strategy-matrix` | E | HISTORICAL REPLAY |
| `historical-context-report` | H | report |
| `telegram-multi-intent-audit` | G | SHADOW |
| `ai-critic-replay-report` | I | SHADOW |

## Train / Val / OOS Discipline

- Split boundaries persisted in `market_events_replay_splits` **before** strategy evaluation
- Fixed ratios: 60% / 20% / 20%
- Verdict `INSUFFICIENT_DATA` when < 10 replay shocks
- No threshold optimization on holdout

## Safety Checklist

- [x] Does not modify `bot.main`
- [x] Does not place live orders
- [x] Does not mutate production `market_events`
- [x] Does not mutate D.1 outcomes
- [x] Does not rebuild signalyp corpus
- [x] Does not delete Telegram inputs
- [x] Does not auto-post to X
- [x] Does not auto-start production backfill

## Mac Mini (after pull)

```bash
cd ~/polymarket-bot/polymarket-btc5m-bot
git pull origin cursor/strategy-discovery-v2
python -m bot.research.market_events market-event-migrate
python -m bot.research.futures_agent migrate
pytest tests/test_market_events_e31.py tests/test_market_events_e32.py tests/test_market_events_e33.py tests/test_market_events_e4.py -q
python -m bot.research.market_events historical-replay-coverage
python -m bot.research.futures_agent telegram-multi-intent-audit
# Do NOT run historical-candle-backfill or restart collectors unless explicitly intended
```
