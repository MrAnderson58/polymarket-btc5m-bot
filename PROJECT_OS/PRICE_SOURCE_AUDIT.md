# PROJECT_OS — Price Source Audit (`shock-paper-core`)

_Read-only. No code modified._

## Verdict

**`shock-paper-core` calls Binance Futures REST directly.** It does **not** use Bybit / OKX / Coinbase for its poll loop.

Bybit exists for **TradFi / multi-venue** universes. OKX exists for **signal-intelligence symbol resolution / exchange context**, not shock-paper polling. **Coinbase is not a price provider** in this stack.

There is **no cross-exchange price fallback** on `core`: if `fapi.binance.com` times out, that symbol’s tick is skipped for the cycle (`fetch_failed++`).

---

## Call chain (shock-paper-core)

```
process_manager: shock-paper-run --universe core --paper-only
    ↓
paper_runner.ShockPaperRunner(universe_mode="core")
    ↓  __init__: self.feed = BinanceFuturesPriceFeed()   ← default, always for core
    ↓  run(): select_universe(mode="core") → CORE_SYMBOLS
    ↓  needs_multi_venue_feed("core") → False            ← MultiVenuePriceFeed NEVER installed
    ↓
run_once → feed.poll_universe(symbols)
    ↓
BinanceFuturesPriceFeed.poll_symbol
    ↓
GET {BINANCE_FUTURES_API}/fapi/v1/ticker/24hr?symbol={SYM}USDT
    ↓
https://fapi.binance.com/...
```

Live log evidence: repeated  
`price poll failed … HTTPSConnectionPool(host='fapi.binance.com', port=443): Read timed out`.

---

## Every price provider

| Provider | Module | Endpoint / API | Used by shock-paper-core? | Used elsewhere |
|----------|--------|----------------|---------------------------|----------------|
| **Binance Futures REST** | `bot/research/market_events/price_feed.py` → `BinanceFuturesPriceFeed` | `https://fapi.binance.com` (`BINANCE_FUTURES_API` in `bot/research/futures/config.py`); `/fapi/v1/ticker/24hr`, `/fapi/v1/premiumIndex` | **Yes — sole poll feed** | Also wrapped inside `MultiVenuePriceFeed` for crypto legs of multi/tradfi modes; doctor ping; G/F exchange context |
| **Bybit V5 REST** | `bot/research/market_events/venue_bybit.py` → `BybitMarketClient` | `https://api.bybit.com` `/v5/market/tickers` | **No** (core path) | `MultiVenuePriceFeed` when `venue=bybit_linear`; `observe-run`; instrument discovery; exchange resolver |
| **OKX** | `bot/research/market_events/signal_intelligence/okx_client.py` | OKX public ticker/symbol checks | **No** | `exchange_resolver` (3rd), `exchange_context` for F0 venue=`okx` |
| **Coinbase** | — | — | **No** | Mentions only in narrative taxonomy strings; **no client / feed** |
| **Binance discovery** | `venue_binance_discovery.py` | Binance exchangeInfo | **No** (not poll) | Resolver / discovery |
| **Reference (Bybit index/mark)** | `reference_provider.py` | Same Bybit ticker fields | **No** on core | TradFi basis / activation; not a separate exchange |

---

## Where each is used (shock-paper vs siblings)

### Shock-paper-core (`--universe core`)

| Site | What runs |
|------|-----------|
| `ShockPaperRunner.__init__` | Instantiates `BinanceFuturesPriceFeed()` |
| `ShockPaperRunner.run` | `needs_multi_venue_feed("core")` is **False** → keeps Binance feed |
| `run_once` | `self.feed.poll_universe(symbols)` → Binance only |
| `select_universe(mode="core")` | Hardcoded `CORE_SYMBOLS` (BTC, ETH, SOL, …) — reason string: `mode=core e1_binance crypto=…` |

### Shock-paper TradFi / multi (`--universe tradfi-liquid|multi-paper|multi`)

| Site | What runs |
|------|-----------|
| `needs_multi_venue_feed` | **True** → replace feed with `MultiVenuePriceFeed(instruments)` |
| Per symbol | `venue == "bybit_linear"` → Bybit; else → **still Binance** `poll_symbol` |

So even multi-venue mode is **route-by-instrument**, not failover: crypto → Binance, TradFi → Bybit.

### Not on the paper poll path (do not confuse)

| Module | Role |
|--------|------|
| `observe-run` / `observation_runner.py` | Bybit-only TradFi observation |
| `exchange_resolver.py` | Resolve listing venue: **Bybit → Binance → OKX** (symbol existence, not shock ticks) |
| `exchange_context.py` | Enrich F0 events with venue metrics (bybit / binance / okx) |
| `doctor.py` | Health ping `fapi.binance.com/fapi/v1/ping` |

---

## Fallback order

### Shock-paper-core price poll

```
Binance Futures ticker/24hr
    ↓ (on failure)
None  — no Bybit/OKX/Coinbase retry
```

Per-symbol: log warning `price poll failed {pair}: …`, omit from `poll_out`, count as `fetch_failed`.

### MultiVenuePriceFeed (not used by core)

```
if instrument.venue == bybit_linear → Bybit
else → Binance
```

No “Binance timed out → try Bybit” logic.

### Exchange symbol resolver (F0 / intelligence only)

```
Bybit → Binance → OKX
```

(`RESOLVER_VENUES = ("bybit", "binance", "okx")` — **Coinbase absent**.)

### Universe selection fallbacks (symbol lists, not venues)

- `multi-paper` / `multi` with empty registry → fall back to `CORE_SYMBOLS` (still Binance-fed when core path applies).

---

## Why shock-paper-core still calls `fapi.binance.com`

1. **By design (Phase E.1):** `core` universe is the original Binance crypto paper path (`price_feed.py` docstring: “Binance futures price feed for Phase E.1”).
2. **Hardcoded default feed:** `ShockPaperRunner` always starts with `BinanceFuturesPriceFeed()`; API base defaults to `BINANCE_FUTURES_API = "https://fapi.binance.com"`.
3. **Multi-venue gate excludes core:** `needs_multi_venue_feed` only true for `multi`, `multi-paper`, `tradfi-liquid`, or explicit symbols — **not** `"core"`.
4. **No failover layer:** Failed Binance polls do not switch venue; architecture treats Binance as the crypto venue of record for core.
5. **Bybit/OKX are parallel products:** Bybit = TradFi/observe/multi routing; OKX = resolver/context — neither is wired into the core poll loop.

---

## Implications (ops)

- Timeouts / geo / rate limits on Binance Futures directly reduce `fetch_ok` for core (seen in `me-shock-paper-core.log`).
- Switching core to Bybit (or adding failover) would be a **product/architecture change**, not a config flip — currently unsupported for `--universe core`.
- Coinbase would require a new venue client; nothing to enable today.

---

## Related PROJECT_OS docs

- `LIVE_WRITE_PATH.md` — what core writes after polls  
- `GATE_ANALYSIS.md` — detector funnel after priced ticks  
- `ARCHITECTURE.md` — universe policy  
