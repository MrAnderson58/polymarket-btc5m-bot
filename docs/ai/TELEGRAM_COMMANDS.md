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
4. **S50:** Before LLM, intelligence passes normalize → dedup → cluster → score → `top_events` (≤8). Full records stay in SQLite.
5. **S51:** `/signals` `/open` `/stats` `/report` `doctor` share `SignalTruthRepository` (S47 opens + S48 history). Direction is machine-locked; reasons must be concrete.
6. **S52:** Report polish — Market Conditions vs Trade Confidence, headline cleanup, ETF/news dedupe, friendly empty stats, pending AI signal when no open trade.

## Related CLI

```bash
python -m bot.research.market_events doctor
python -m bot.research.ai_analyst report
```
