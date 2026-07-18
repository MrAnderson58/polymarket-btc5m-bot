# Roadmap — AI Trading Terminal

## Сейчас

- ✅ **V7.1 Alpha** — Terminal foundation (Scanner → Decision → Portfolio → Research → Brief)

## Следующее

- □ **Alpha Certification** — полный системный аудит (architecture / coverage / perf / security)
- □ **Paper Alpha** — ежедневное использование в paper-режиме
- □ **Multi-exchange** — единый слой под несколько площадок
- □ **Bybit Live**
- □ **Binance Live**
- □ **Portfolio Manager**
- □ **AI Risk Manager**
- □ **AI Position Sizing**
- □ **AI Execution**
- □ **Mobile Dashboard**
- □ **Web Terminal**
- □ **V8 Beta**

## Принципы

1. Сначала paper, потом live.
2. Claude ревьюит готовые DecisionCard — не строит идею с нуля.
3. Terminal UI не обходит Command Dispatcher / Services.
4. Learning и Execution остаются отдельными слоями.

## Связанные документы

- [CHANGELOG.md](../CHANGELOG.md)
- [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- [bot/terminal/README.md](../bot/terminal/README.md)
