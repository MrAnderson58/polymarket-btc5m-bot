# Telegram Commands (S49 Trader UI)

Signal-centric Telegram terminal for the AI Trading Platform.

## Trader commands (primary)

| Command | Purpose |
|---------|---------|
| `/report` | Compact signal card: direction, levels, score, macro/flow, top news, AI verdict |
| `/signals` | Recent paper signals |
| `/open` | Open paper trades |
| `/stats` | Strategy performance stats |
| `/doctor` | Platform health (DB, feeds, AI keys, Telegram, paper) |

Inline keyboard after `/report`:

- 📈 Report
- 📊 Signals
- 📂 Open
- 📉 Stats
- ⚙ Doctor

## Debug / Admin

Full analytics are **not** shown on the trader path. Use:

```text
/debug report
```

Also available (hidden from trader menu):

`/btc` `/macro` `/sp500` `/events` `/narrative` `/context` `/health`
`/market` `/closed` `/leaderboard` `/daily`

## Design rules

1. Human sees **signal + essentials** only (~30–40 lines).
2. Full text, AI summary, embeddings, entities, categories, impact, links, reasoning, and market reaction stay in storage for AI / learning / ranking / validation.
3. News lines are `icon + headline` only (🟢 / 🟡 / 🔴) — no article bodies.

## Related CLI

```bash
python -m bot.research.market_events doctor
python -m bot.research.ai_analyst report
```
