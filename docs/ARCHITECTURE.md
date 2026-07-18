# Architecture — AI Trading Terminal

Текущая архитектура V7.1 Alpha. Terminal — presentation / orchestration слой поверх существующих research / learning / paper сервисов.

## Слои

```
Telegram
    ↓
Terminal (Router · UI · Session · Telemetry)
    ↓
Command Dispatcher
    ↓
Services
    ├── Decision
    ├── Scanner
    ├── Portfolio
    ├── Research
    ├── Watchlist / Alerts / Brief / Timeline
    ↓
Learning
    ↓
Execution (PaperAdapter сегодня; live позже)
    ↓
Exchange
```

## Поток пользователя (E2E)

```
/start → Home
       → Signals
       → Decision BTC
       → Watchlist (⭐)
       → Portfolio
       → Morning Brief
       → Research Review
       → Timeline / Why
```

Inline-переходы идут через `editMessageText` (без спама новыми сообщениями).

## Ответственность слоёв

| Слой | Делает | Не делает |
|------|--------|-----------|
| **Telegram / Router** | parse slash & `term:*` callbacks | прямые вызовы Service / SQL |
| **Command Dispatcher** | Navigate / ToggleWatch / Execution stubs | бизнес-скоринг |
| **Screen Controller** | сборка экранов, session/timeline/telemetry | знание Telegram HTTP |
| **Services** | Scanner, Decision, Portfolio, Research… | UI-разметка |
| **Learning** | paper outcomes, winrate hints | исполнение ордеров |
| **Execution** | PaperAdapter / будущий live | Telegram / Claude prompts |
| **Exchange** | venue IO | Terminal state |

## Ключевые пакеты

```
bot/terminal/
  telegram/      # Router, keyboards, render
  commands/      # Dispatcher, ScreenController, UI commands
  session/       # per-chat state
  telemetry/     # timings & screen counters
  scanner/       # Universal Scanner + ranking
  decision/      # DecisionCard builder
  explain/       # ScoreExplanation (/why)
  portfolio/     # Portfolio Intelligence
  research/      # Claude / template review
  brief/         # Morning Brief
  watchlist/     # Favorites
  alerts/        # Rules
  timeline/      # Symbol events
  execution/     # PaperAdapter gate
  events/        # Sync EventBus
```

## Инварианты

1. **Router → Dispatcher → Services** — без обхода слоёв.
2. **Scanner / Decision / Execution не знают Telegram.**
3. **Research** получает готовый контекст (DecisionCard + Scanner + Portfolio + News + Learning) и делает review, не blank-slate analysis.
4. **G3.1** — только read-only источник для Crypto Scanner Provider.
5. **Live execution** выключен до Paper Alpha / явного enable.

## Связанные документы

- [ROADMAP.md](ROADMAP.md)
- [CHANGELOG.md](../CHANGELOG.md)
- [bot/terminal/README.md](../bot/terminal/README.md)
