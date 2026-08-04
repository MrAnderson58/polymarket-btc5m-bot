# DECISION_THRESHOLD_REPORT

_Decision Threshold Optimizer V1 — research only._

- grid size: 31104
- evaluated: 31104
- cache n: 19205
- runtime: **683.732s**

## Before (baseline)

```
{
  "thresholds": {
    "min_supporting": 2,
    "min_confidence": 0.45,
    "min_fingerprint": 5.0,
    "min_timeline": 30.0,
    "min_hist_wr": 55.0,
    "min_hist_pf": 1.15,
    "min_rules": 0
  },
  "label": "sup>=2 conf>=45% fp>=5% tl>=30% wr>=55% pf>=1.15 rules>=0",
  "trades": 4111,
  "wr": 76.23,
  "pf": null,
  "pf_inf": true,
  "ev": 1.5337,
  "sharpe": 1.0103,
  "max_dd": 0.0,
  "total": 6305.2284,
  "accepted": 4111,
  "rejected": 15094,
  "false_rejects": 5586,
  "false_approvals": 977,
  "precision": 0.7623,
  "recall": 0.3594,
  "f1": 0.4885,
  "tp": 3134,
  "fp": 977,
  "fn": 5586,
  "tn": 9508,
  "top_rejection_reason": "confidence"
}
```

## Best profile

```
{
  "thresholds": {
    "min_supporting": 1,
    "min_confidence": 0.2,
    "min_fingerprint": 30.0,
    "min_timeline": 50.0,
    "min_hist_wr": 70.0,
    "min_hist_pf": 1.0,
    "min_rules": 0
  },
  "label": "sup>=1 conf>=20% fp>=30% tl>=50% wr>=70% pf>=1 rules>=0",
  "trades": 2246,
  "wr": 89.27,
  "pf": null,
  "pf_inf": true,
  "ev": 1.8038,
  "sharpe": 1.2545,
  "max_dd": 0.0,
  "total": 4051.3146,
  "accepted": 2246,
  "rejected": 16959,
  "false_rejects": 6715,
  "false_approvals": 241,
  "precision": 0.8927,
  "recall": 0.2299,
  "f1": 0.3656,
  "tp": 2005,
  "fp": 241,
  "fn": 6715,
  "tn": 10244,
  "top_rejection_reason": "hist_wr/pf",
  "delta_vs_baseline_trades": -1865
}
```

## Profiles

### Aggressive
- trades=2287 WR=89.16 PF=— EV=1.7993 Sharpe=1.2473 MaxDD=0.0
- rejected=16918 accepted=2287
- top rejection=hist_wr/pf
- `sup>=1 conf>=20% fp>=30% tl>=30% wr>=70% pf>=1 rules>=0`

### Balanced
- trades=2246 WR=89.27 PF=— EV=1.8038 Sharpe=1.2545 MaxDD=0.0
- rejected=16959 accepted=2246
- top rejection=hist_wr/pf
- `sup>=1 conf>=20% fp>=30% tl>=50% wr>=70% pf>=1 rules>=0`

### Conservative
- trades=744 WR=93.28 PF=— EV=1.682 Sharpe=1.2318 MaxDD=0.0
- rejected=18461 accepted=744
- top rejection=confidence
- `sup>=1 conf>=50% fp>=40% tl>=50% wr>=70% pf>=1 rules>=0`

### Very Conservative
- trades=744 WR=93.28 PF=— EV=1.682 Sharpe=1.2318 MaxDD=0.0
- rejected=18461 accepted=744
- top rejection=confidence
- `sup>=1 conf>=50% fp>=40% tl>=50% wr>=70% pf>=1 rules>=1`

## Top 100 false rejects

- n false rejects: 5586
- top100 pnl left on table: 2130.7558
- binding counts: `{'min_hist_wr': 65, 'hard_veto': 19, 'structure': 11, 'min_supporting': 5}`
- EV recovered if relaxed: `{'min_hist_wr': 0.071139, 'hard_veto': 0.029984, 'structure': 0.007639, 'min_supporting': 0.002186}`
- mean pnl if relaxed: `{'min_hist_wr': 21.0188, 'hard_veto': 30.3076, 'structure': 13.3366, 'min_supporting': 8.3967}`
