# COUNTERFACTUAL_REPORT

Portfolio ablation: metrics if high-contribution trades for a feature are removed.

| feature | ΔEV | ΔWR | ΔPF | n_ablated |
|---|---|---|---|---|
| `funding` | None | None | None | 50 |
| `oi` | -2.325486 | 4.0 | -0.07364 | 25 |
| `atr` | None | None | None | 50 |
| `volume` | 0.0 | 0.0 | 0.0 | 0 |
| `fear` | 0.0 | 0.0 | 0.0 | 0 |
| `news` | 0.0 | 0.0 | 0.0 | 0 |
| `rsi` | 0.0 | 0.0 | 0.0 | 0 |
| `ema` | 0.0 | 0.0 | 0.0 | 0 |

## Per-trade examples

### trade_id=3 primary=funding

```json
{
  "funding": {
    "contribution_pct": 52.0184,
    "observed_pnl": -213.0761,
    "without_feature_pnl": -102.237322,
    "delta_pnl": 110.838778
  },
  "oi": {
    "contribution_pct": 7.5421,
    "observed_pnl": -213.0761,
    "without_feature_pnl": -197.005687,
    "delta_pnl": 16.070413
  },
  "atr": {
    "contribution_pct": 21.4445,
    "observed_pnl": -213.0761,
    "without_feature_pnl": -167.382996,
    "delta_pnl": 45.693104
  },
  "volume": {
    "contribution_pct": 0.0,
    "observed_pnl": -213.0761,
    "without_feature_pnl": -213.0761,
    "delta_pnl": 0.0
  },
  "news": {
    "contribution_pct": 0.0,
    "observed_pnl": -213.0761,
    "without_feature_pnl": -213.0761,
    "delta_pnl": 0.0
  }
}
```

### trade_id=9 primary=funding

```json
{
  "funding": {
    "contribution_pct": 52.0184,
    "observed_pnl": -42.1907,
    "without_feature_pnl": -20.243773,
    "delta_pnl": 21.946927
  },
  "oi": {
    "contribution_pct": 7.5421,
    "observed_pnl": -42.1907,
    "without_feature_pnl": -39.008635,
    "delta_pnl": 3.182065
  },
  "atr": {
    "contribution_pct": 21.4445,
    "observed_pnl": -42.1907,
    "without_feature_pnl": -33.143115,
    "delta_pnl": 9.047585
  },
  "volume": {
    "contribution_pct": 0.0,
    "observed_pnl": -42.1907,
    "without_feature_pnl": -42.1907,
    "delta_pnl": 0.0
  },
  "news": {
    "contribution_pct": 0.0,
    "observed_pnl": -42.1907,
    "without_feature_pnl": -42.1907,
    "delta_pnl": 0.0
  }
}
```

### trade_id=13 primary=funding

```json
{
  "funding": {
    "contribution_pct": 52.0184,
    "observed_pnl": -234.1488,
    "without_feature_pnl": -112.348341,
    "delta_pnl": 121.800459
  },
  "oi": {
    "contribution_pct": 7.5421,
    "observed_pnl": -234.1488,
    "without_feature_pnl": -216.489063,
    "delta_pnl": 17.659737
  },
  "atr": {
    "contribution_pct": 21.4445,
    "observed_pnl": -234.1488,
    "without_feature_pnl": -183.936761,
    "delta_pnl": 50.212039
  },
  "volume": {
    "contribution_pct": 0.0,
    "observed_pnl": -234.1488,
    "without_feature_pnl": -234.1488,
    "delta_pnl": 0.0
  },
  "news": {
    "contribution_pct": 0.0,
    "observed_pnl": -234.1488,
    "without_feature_pnl": -234.1488,
    "delta_pnl": 0.0
  }
}
```

### trade_id=14 primary=funding

```json
{
  "funding": {
    "contribution_pct": 52.0184,
    "observed_pnl": -178.022,
    "without_feature_pnl": -85.417804,
    "delta_pnl": 92.604196
  },
  "oi": {
    "contribution_pct": 7.5421,
    "observed_pnl": -178.022,
    "without_feature_pnl": -164.595403,
    "delta_pnl": 13.426597
  },
  "atr": {
    "contribution_pct": 21.4445,
    "observed_pnl": -178.022,
    "without_feature_pnl": -139.846072,
    "delta_pnl": 38.175928
  },
  "volume": {
    "contribution_pct": 0.0,
    "observed_pnl": -178.022,
    "without_feature_pnl": -178.022,
    "delta_pnl": 0.0
  },
  "news": {
    "contribution_pct": 0.0,
    "observed_pnl": -178.022,
    "without_feature_pnl": -178.022,
    "delta_pnl": 0.0
  }
}
```

### trade_id=17 primary=funding

```json
{
  "funding": {
    "contribution_pct": 52.0184,
    "observed_pnl": -74.1754,
    "without_feature_pnl": -35.590544,
    "delta_pnl": 38.584856
  },
  "oi": {
    "contribution_pct": 7.5421,
    "observed_pnl": -74.1754,
    "without_feature_pnl": -68.581017,
    "delta_pnl": 5.594383
  },
  "atr": {
    "contribution_pct": 21.4445,
    "observed_pnl": -74.1754,
    "without_feature_pnl": -58.268856,
    "delta_pnl": 15.906544
  },
  "volume": {
    "contribution_pct": 0.0,
    "observed_pnl": -74.1754,
    "without_feature_pnl": -74.1754,
    "delta_pnl": 0.0
  },
  "news": {
    "contribution_pct": 0.0,
    "observed_pnl": -74.1754,
    "without_feature_pnl": -74.1754,
    "delta_pnl": 0.0
  }
}
```
