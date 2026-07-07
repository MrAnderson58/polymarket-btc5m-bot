# Bid/Ask Semantics Audit — BTC 5m Polymarket Research

**Date:** 2026-07-07  
**Scope:** End-to-end quote orientation from CLOB through research simulators  
**Execution impact:** Live execution unchanged (research normalizes on read)

---

## Executive summary

| Question | Answer |
|----------|--------|
| Are bid and ask reversed? | **Yes** in `market_scanner.get_token_quotes` |
| Only logging reversed? | **No** — wrong labels persist to DB |
| Spread formula wrong? | Formula `ask - bid` is correct; **inputs are swapped** |
| Historical observations affected? | **Yes** — essentially all `v4_shadow_observations` from main bot |
| Simulator results valid? | **Require recomputation** with normalized quotes |

Production log `YES 0.330/0.320 spread=-0.010` means stored `yes_bid` > `yes_ask`.

---

## Root cause

`bot/market_scanner.py:get_token_quotes` (lines 180–193) maps:

- `bid` ← CLOB `side=SELL`
- `ask` ← CLOB `side=BUY`

Correct semantics (`bot/research/mtf/quotes.py:3–8`):

- `BUY` → best bid
- `SELL` → best ask

---

## Data flow

```
CLOB get_price
  → market_scanner.get_token_quotes (REVERSED)
  → get_best_bid_ask / _quote_cache
  → main._v4_cycle
  → shadow_trade.record_v4_observation_tick
  → observe.build_observation / compute_spread (yes_ask - yes_bid → negative)
  → database.insert_v4_shadow_observation
  → load_market_observations (NOW: normalize on read)
  → strategy_simulator / market_behavior
```

---

## Layer impact

| Layer | File | Impact |
|-------|------|--------|
| Scanner | `market_scanner.py` | Source of reversal |
| V4 persist | `observe.py`, `shadow_trade.py` | Negative spread in DB |
| Simulator entry | `simulator.py:_entry_ask` | Uses mislabeled ask (= true bid) |
| Simulator exit | `simulator.py:_forward_exit` | Uses mislabeled bid (= true ask) |
| Spread filter | `features.py:_side_spread` | `max(0,…)` masked reversal → 0 |
| MTF research | `mtf/quotes.py` | **Correct** (separate path) |

---

## Research fix

`bot/research/quote_semantics.py` + `load_market_observations(normalize_quotes=True)`.

Raw DB unchanged. Execution unchanged.

---

## Detection

```bash
python -m bot.research.strategy_simulator quote-audit
```

```sql
SELECT COUNT(*) FROM v4_shadow_observations
WHERE yes_bid > yes_ask;
```

---

## Recommendations

1. Re-run `discover` / `walk-forward` on dense-era with market filters
2. Use `prepare-forward` + `forward-track` for observe-only validation
3. Minimum **30 forward signals/strategy** before real-money review
4. Scanner fix is a **separate execution-reviewed change** (not done here)
