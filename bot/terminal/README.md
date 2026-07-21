# AI Trading Terminal (V6)

Foundation + interactive Telegram navigation. **Additive** — old slash commands
remain; overlapping navigation commands are served by Terminal.

## Architecture

```
Telegram (/start, callbacks)
      │
      ▼
futures_agent.telegram_inbound  (callback_query + terminal slash intercept)
      │
      ▼
terminal_router  →  render (components)  →  services / execution / events
```

Inline navigation uses `editMessageText` (no new messages on button taps).

```
Telegram
      │
      ▼
Commands / Screens
      │
Dispatcher (execution stubs)
      │
ExecutionService → PaperAdapter
      │
EventBus → Handlers (stubs)
```

## Layers

| Layer | Role |
|--------|------|
| **Telegram UI** | `/start` menu, inline keyboards, `editMessageText` navigation |
| **Components** | `CardRenderer`, `SectionRenderer`, `ProgressBarRenderer`, `StatusRenderer` |
| **Formatters** | money / pct / duration / progress bar / status badges |
| **Services** | Read-only DTOs from existing paper/health/candidates |
| **Execution** | PaperAdapter contract (mutate gated) |
| **Events** | Sync EventBus |

## Commands

Terminal-owned navigation: `/start` `/home` `/markets` `/signals` `/positions`
`/portfolio` `/account` `/settings` `/watch` `/alert`

### AI Research Trader UI (S49)

Primary (signal-centric): `/report` `/signals` `/open` `/stats` `/doctor`

Debug full analytics: `/debug report`

See [`docs/ai/TELEGRAM_COMMANDS.md`](../../docs/ai/TELEGRAM_COMMANDS.md).

Watchlist: `/watch` `/watch add BTC` `/watch remove ETH` + ⭐ Favorite on signals.

Alerts: `/alert` `/alert add BTC score>85` `/alert add NVDA LONG`

Decision: `/decision BTC` → DecisionCard (idea, not raw score)

Morning Brief: `/brief` (+ `/brief ai` to enrich summary via Claude)

Research: `/research BTC` — Claude reviews formed DecisionCard

Execution stubs: `/open` `/close` `/risk` `/sl` `/tp`

All other legacy commands still go through market_events router.

## Universal Scanner (V6.2.0)

`bot/terminal/scanner/` — single scan API over providers:

- `ScannerProvider.scan / supports / priority`
- `CryptoScannerProvider` → existing G3.1 (`fetch_top_candidates_g31`) only
- `StaticScannerProvider` → tests
- `ScannerRegistry` + `ScannerService.scan / scan_all / top / by_symbol`
- Unified ranking → `score` 0…100

G3.1 is not modified — read-only source.

## Watchlist Engine (V6.2.1)

`bot/terminal/watchlist/` — per-user Favorites:

- `WatchlistService.get / add / remove / toggle / is_favorite`
- JSON persistence under `data/terminal/watchlists.json`
- Default seed: BTC, ETH, NVDA, TSLA, GOLD
- Telegram: `/watch`, `/watch add`, `/watch remove`, ⭐ on signal cards

## Alert Engine (V6.2.2)

`bot/terminal/alerts/` — Price / Signal / AI rules:

- `AlertService.create / list / evaluate / parse_and_create`
- `signal_alert` (score / direction), `ai_alert`, `price_alert`
- Evaluates against `ScannerService` snapshot
- Telegram: `/alert`, `/alert add BTC score>85`, `/alert add NVDA LONG`

## Instrument Registry (V6.1.1)

`bot/terminal/instruments/` — unified catalog:

- `Instrument` + `AssetClass`
- `InstrumentRegistry.get / list / exists`
- `StaticProvider` seed (BTCUSDT, ETHUSDT, SOLUSDT, NVDA, AAPL, META, QQQ, SPY, GLD, XAUUSD, CL, EURUSD)

## Market Profile Engine (V6.1.2)

Descriptive profiles per asset class (not trading logic):

`CryptoFutureProfile`, `StockProfile`, `CommodityProfile`, `ForexProfile`, …

Fields: 24/7, trading hours, funding, pre/post market, macro, earnings, options, liquidations.

## Decision Card (V7.0.0)

`bot/terminal/decision/` — trader-facing idea over Scanner:

- `DecisionCard` — direction, levels, reasons, warnings, `ai_summary`
- Builder: ScannerResult + MarketProfile + Signal + Learning → card
- Explain: ✓ / ⚠ bullets (template, no Claude/GPT)
- `DecisionService.for_symbol` / `top`
- Telegram: `/decision BTC`

## Portfolio Intelligence (V7.0.1)

`bot/terminal/portfolio/` — analysis, not a trade list:

- Open Risk, Correlation, Sector / Crypto Exposure, Cash, Expected DD
- Template Portfolio Advice (e.g. 72% Crypto → reduce BTC, add Gold)
- Wired into `/portfolio` screen

## Morning Brief (V7.0.2)

`bot/terminal/brief/` — daily Telegram package:

Good Morning · Markets · Stocks · Crypto · Macro · Top Opportunities ·
Portfolio Advice · Watchlist Updates · AI Summary

- `/brief` builds from Scanner / Decision / Portfolio / Watchlist
- `MorningBriefService.deliver(chat_id)` for cron / morning push

## AI Research Integration (V7.0.3)

`bot/terminal/research/` — Claude reviews formed context (not blank-slate):

Inputs: DecisionCard + Scanner + Portfolio + News + Learning  
Output: confirm / caution / reject + adjustments

- Uses existing G.2 Claude client + Telegram channel gate
- Template fallback when Claude unavailable
- `/research BTC` · `/brief ai`

## E2E User Flow (V7.1.0)

Inline-only path (editMessageText):

`/start → Home → Signals → Decision BTC → Watchlist → Portfolio → Brief → Research`

- Router is thin: parse → `CommandDispatcher.dispatch_ui`
- ScreenController owns service calls (no services in Telegram Router)
- Session + Timeline + Telemetry updated on each hop

## Terminal Session (V7.1.1)

`bot/terminal/session/` — `get_session` / `update_session` / `clear_session`

Stores: screen, symbol, last_decision, filter, language, timezone.

## Market Timeline (V7.1.2)

`bot/terminal/timeline/` — read-only event log per symbol (`/timeline BTC`).

## Explainability (V7.1.3)

`ScoreExplanation` + `/why BTC` — score broken into Trend/Learning/Pattern/…

## Terminal Telemetry (V7.1.4)

`bot/terminal/telemetry/` — decision/scan timings, signals viewed, screen frequencies.

## Version

`7.1.4` — Terminal Telemetry.
`7.1.3` — Explainability.
`7.1.2` — Market Timeline.
`7.1.1` — Terminal Session.
`7.1.0` — End-to-End User Flow.
`7.0.3` — AI Research Integration.
`7.0.2` — Morning Brief.
`6.2.2` — Alert Engine.
`6.2.1` — Watchlist Engine.
`6.2.0` — Universal Scanner.
`6.1.2` — Market Profile Engine.
`6.1.1` — Instrument Registry.
`6.1.0` — Interactive Telegram Terminal (nav + edit_message).
`6.0.3` — Event Bus & Command Pipeline.
`6.0.2` — Execution abstraction.
`6.0.1` — live read-only screens.
`6.0.0` — foundation.
