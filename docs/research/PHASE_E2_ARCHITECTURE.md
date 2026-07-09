# Phase E.2 — Multi-Asset Tokenized Markets (Additive)

**Status:** E.2 shipped in `638a4a7`; E.2.1 adds safe activation and honest index discovery.

## Index / ETF Proxy Discovery (E.2.1 — corrected)

**Root cause of `bybit_index: 0` on Mac Mini:** E.2 assumed `US500USDT` and `US100USDT` exist on Bybit linear. They do **not** (API retCode=10001). There is also **no** `symbolType=index` category (returns 0 instruments).

**Actual API findings (2026-07):**
| Symbol | Status | Notes |
|--------|--------|-------|
| US500USDT | NOT FOUND | Documentation assumption was wrong |
| US100USDT | NOT FOUND | Documentation assumption was wrong |
| SPYUSDT | Trading, symbolType=stock | S&P 500 ETF proxy (~$751) |
| QQQUSDT | Trading, symbolType=stock | Nasdaq 100 ETF proxy (~$724) |
| SPXUSDT | Trading | **Meme token ~$0.37 — NOT S&P 500; excluded** |

Run `python -m bot.research.market_events index-discovery-audit` for live evidence.

## Activation Tiers (E.2.1)

| Tier | observe-run | shock-paper-run |
|------|-------------|-----------------|
| PAPER_ACTIVE | yes | yes (if rules pass) |
| WATCH | yes | **no** |
| INACTIVE | no | no |

`instrument-discover --enable-tradfi` applies measured rules (turnover, spread, indexPrice, basis sanity, session, observation polls). It does **not** blind-activate all LIQUID instruments.

## Universe Modes

| Mode | Description |
|------|-------------|
| `core` | E.1 Binance crypto (unchanged) |
| `tradfi-liquid` | PAPER_ACTIVE TradFi only |
| `multi-paper` | PAPER_ACTIVE crypto + TradFi |
| `multi` | Legacy active CORE/LIQUID |
| `--symbols XAUUSDT,NVDAUSDT` | Explicit registry lookup |

## Reference Price Quality

TradFi reference = `REFERENCE_PROVIDER_BYBIT_INDEX` (Bybit indexPrice). **Same venue as lastPrice** — measures intra-venue divergence, not true external-market basis. Future: `REFERENCE_PROVIDER_EXTERNAL_EQUITY`, `REFERENCE_PROVIDER_EXTERNAL_COMMODITY` (not wired in E.2.1).

## Mac Mini validation (two terminals)

```bash
# Terminal 1 — E.1 crypto paper (unchanged)
python -u -m bot.research.market_events shock-paper-run --universe core --paper-only

# Terminal 2 — TradFi observation only (default, no crypto duplicate)
python -u -m bot.research.market_events observe-run --universe tradfi-observe

# Reports
python -m bot.research.market_events observe-report
python -m bot.research.market_events activation-explain
python -m bot.research.market_events activation-explain --symbol NVDA
```

## What E.1 Already Had (unchanged)

- Binance futures REST polling for `CORE_SYMBOLS`
- Shock detectors SHOCK_A–E, reversals R1–R5, exits EXIT_A–E
- Isolated `data/market_events.db`
- Basic crypto classification: `MARKET_WIDE`, `SECTOR_WIDE`, `ASSET_SPECIFIC`, `UNKNOWN`
- Read-only context links to Telegram / Polymarket / futures_agent

## E.2 Additions

### Task A — Instrument master

Table `market_events_instruments` with venue discovery provenance in `metadata_json`.

CLI: `instrument-discover`, `instrument-report`

### Task B — Multi-asset universe

- **Binance:** crypto USDT perpetuals (same core set as E.1)
- **Bybit linear:** stock perps, commodity perps, index proxies (US500, US100)
- Instruments are **discovered from APIs**, not invented
- TradFi instruments default `active=0`; enable with `--enable-tradfi` after liquidity review

### Task C — Reference price / basis

- Trade price: venue last
- Reference: Bybit `indexPrice` for TradFi; self for crypto
- `basis_bps`, `reference_return_pct`, snapshot `tracking_error_bps`
- Module: `basis_monitor.py`, `multi_venue_feed.py`

### Task D — Session regimes

`session_regime.py` — PREMARKET, US_REGULAR, AFTER_HOURS, UNDERLYING_CLOSED, WEEKEND, CRYPTO_24H

Stored on `market_events.session_regime`.

### Task E — News entity graph (architecture only)

Static registry in `entity_graph.py` → `market_events_entity_registry`.  
No AI classification or trade placement.

### Task F — Cross-asset classification

`cross_asset_classifier.py` with fixed research thresholds (not optimized on sample):

`CRYPTO_MARKET_WIDE`, `EQUITY_MARKET_WIDE`, `TECH_SECTOR_WIDE`, `COMMODITY_GEOPOLITICAL`, `ASSET_SPECIFIC`, `TOKENIZED_MARKET_DISLOCATION`, `UNKNOWN`

### Task G — Stratified reporting

Reports split by `asset_class`, `session_regime`, `cross_classification` — never pooled headline expectancy.

### Task H — Preservation

- No changes to `trades.db`, Phase C/D tables, live execution, parser
- E.2 schema is additive (`SCHEMA_VERSION=2`)

## Venue / API Limitations

| Limitation | Impact |
|------------|--------|
| Bybit TradFi only (no Yahoo/Refinitiv) | Reference = Bybit index; may be stale outside US hours |
| No xStock spot auto-enable | Stock perps preferred over TSLAX-style spot |
| Liquidity gates | TradFi needs turnover ≥ $1M; crypto ≥ $50M quote volume |
| Binance only for crypto in E.2 | Bybit crypto not duplicated to avoid dual feeds |
| `MACRO_EVENT_WINDOW` | Reserved constant; not auto-detected in E.2 |

## Recommended paper monitoring order

1. **Phase 1:** `shock-paper-run --universe core` (E.1 Binance crypto — production path)
2. **Phase 2:** `instrument-discover` → review `instrument-report` → `instrument-discover --enable-tradfi`
3. **Phase 3:** `shock-paper-run --universe multi` after TradFi liquidity sign-off

## Mac Mini validation

```bash
cd ~/polymarket-bot/polymarket-btc5m-bot && git pull origin cursor/strategy-discovery-v2
python -m bot.research.market_events market-event-migrate
python -m bot.research.market_events instrument-discover
python -m bot.research.market_events instrument-report
python -m bot.research.market_events shock-paper-run --universe core --paper-only --max-cycles 2
python -m bot.research.market_events shock-paper-run --universe multi --paper-only --max-cycles 2
pytest tests/test_market_events_e1.py tests/test_market_events_e2.py -q
```

## Out of scope (E.2)

- Live trading
- Automatic LLM trade decisions
- X/Twitter posting
- Threshold optimization on evaluation sample
- External equity reference feeds (IEX, Polygon, etc.)
