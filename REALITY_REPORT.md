# REALITY_REPORT

_Reality Validation Engine V1 — research only. Attempts to DISPROVE the system._

- runtime: **35.545s**
- n_trades: **19205**
- source: `combined`
- reality_score: **85.98** / 100
- overfitting_score: **5.5** / 100
- largest_weakness: **stress_weakest:half_liquidity**

## Walk-Forward
```json
{
  "ok": true,
  "n_folds": 766,
  "train_mean_pnl": 8199.487282,
  "val_mean_pnl": 1334.290501,
  "gap": 0.110065,
  "val_positive_rate": 0.9804
}
```

## Out-of-Sample
```json
{
  "ok": true,
  "n_old": 13443,
  "n_new": 5762,
  "gap_expectancy": 0.200915,
  "gap_sharpe": 0.631026,
  "degradation": false
}
```

## Monte Carlo
```json
{
  "ok": true,
  "n_sims": 10000,
  "fragile": false,
  "mixed": {
    "mean": 11878.633172,
    "median": 11329.6062,
    "ci_5": 8145.78976,
    "ci_95": 15803.77666,
    "p_profit": 1.0,
    "p_loss": 0.0
  },
  "max_dd_mean": -2.20832
}
```
