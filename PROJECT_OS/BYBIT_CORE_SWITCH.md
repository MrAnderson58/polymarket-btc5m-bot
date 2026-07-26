# PROJECT_OS — Bybit core switch

Default price provider for `shock-paper-core` is now **Bybit**, not Binance Futures.

## Goal

- `shock-paper-core` must not call `fapi.binance.com`
- Keep `BinanceFuturesPriceFeed` in repo (still used by `MultiVenuePriceFeed`)
- Use existing `BybitMarketClient`
- Optional OKX last-price fallback when Bybit misses
- No detector / threshold / schema / strategy / paper-trading logic changes

## Changed files

| File | Change |
|------|--------|
| `bot/research/market_events/bybit_price_feed.py` | **New** — `BybitPriceFeed` with same poll surface as Binance feed (`poll_symbol` / `poll_universe` / `get_state` / `ensure_symbol`) |
| `bot/research/market_events/paper_runner.py` | Default `self.feed = BybitPriceFeed()` (was `BinanceFuturesPriceFeed()`) |
| `bot/research/market_events/universe.py` | Core selection reason `e1_binance` → `e1_bybit` (logging only) |

### Not changed

- `bot/research/market_events/price_feed.py` (`BinanceFuturesPriceFeed` retained)
- `bot/research/market_events/multi_venue_feed.py` (TradFi/multi still routes crypto via Binance wrapper)
- Detectors, thresholds, schemas, pending/paper SQL, filters

## Fallback order (core poll)

```
Bybit V5 linear ticker  (api.bybit.com /v5/market/tickers)
    ↓ on miss / error
OKX USDT-SWAP last      (okx_client.fetch_ticker_metrics)  [optional, default on]
    ↓ on miss
None → fetch_failed for that symbol (same as before)
```

No Binance hop on the core path.

## Verification

| Check | Result |
|-------|--------|
| Default runner feed | `ShockPaperRunner(max_cycles=0).feed` → **`BybitPriceFeed`** |
| Live Bybit poll (BTC/ETH/SOL/XRP) | **4/4 OK** (e.g. BTC ≈ 64338) |
| `BinanceFuturesPriceFeed` still importable | **Yes** |
| `tests/test_market_events_e1.py` | **13 passed** |
| Restarted `shock-paper-core` | PID **27534** |
| Universe log | `mode=core e1_bybit crypto=10` |
| Post-restart log `fapi.binance.com` | **0 mentions** |
| Post-restart `price poll failed` | **0** (sample window) |
| `lsof` Binance host | **False** |

## Ops note

Existing long-lived core processes must be restarted to pick up the default (done once for verification). TradFi / `multi*` runners still use `MultiVenuePriceFeed` (Bybit for `bybit_linear`, Binance for other crypto legs).

## Related

- `PRICE_SOURCE_AUDIT.md` — pre-switch audit (Binance was sole core feed)
