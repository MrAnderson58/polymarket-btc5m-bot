# EDGE_DISCOVERY_REPORT

_Edge Discovery Engine V1 — combinatorial mathematics only. No Gate / Optimizer / Strategy / Paper / Execution changes._

- Trades analyzed: **50** (S42 INNER JOIN S55)
- Combos tested: **1252** (atoms=31)
- Min n applied: **10** (research target=100)
- READY=0 TEST=0
- Baseline PF=0.4379 EV=-25.2943 WR=36.0 Sharpe=-0.2754
- Elapsed: 0.105s

## TOP-100 Edge Scoreboard

| Rank | Edge score | Rule | n | PF | EV | WR | p | CI | Status |
|---:|---:|---|---:|---:|---:|---:|---:|---|---|
| — | — | _(no edges passed filters on this corpus)_ | — | — | — | — | — | — | — |

## Clusters

- **Liquidity**: n_rules=0 best_score=0.0
- **Momentum**: n_rules=0 best_score=0.0
- **Mean Reversion**: n_rules=0 best_score=0.0
- **Breakout**: n_rules=0 best_score=0.0
- **Trend**: n_rules=0 best_score=0.0
- **Panic**: n_rules=0 best_score=0.0
- **Volatility Expansion**: n_rules=0 best_score=0.0
- **Compression**: n_rules=0 best_score=0.0

## Notes

- Filters: n floor, PF/EV vs baseline, p≤0.05, FDR, instability, overlap>80%.
- Status READY requires research n≥100 + FDR + WF + OOS + stability.
- Status TEST is exploratory on smaller local books.

Artifacts under `reports/research/edge_discovery/`.
