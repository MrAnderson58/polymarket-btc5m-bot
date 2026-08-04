# DECISION_ERROR_REPORT

_Decision Error Learning Engine V1 — research only._

- n: 19205
- runtime: **1.234s**
- confusion: `{'FN': 5586, 'TN': 9508, 'FP': 977, 'TP': 3134}`

## Largest error source
```
{
  "module": "edge",
  "false_reject_n": 4496,
  "false_accept_n": 0,
  "false_reject_pct": 80.49,
  "false_accept_pct": 0.0,
  "recovered_ev": 2.1024,
  "recovered_pf": null,
  "recovered_wr": 100.0,
  "recovered_total_pnl": 9452.2844,
  "recovered_sharpe": 0.4371,
  "error_score": 10257.1844
}
```

## Suggestions
```
[
  {
    "module": "edge",
    "verdict": "too strict",
    "false_reject_pct": 80.49,
    "false_accept_pct": 0.0,
    "recovered_ev": 2.1024,
    "note": "largest error source"
  },
  {
    "module": "confidence",
    "verdict": "too strict",
    "false_reject_pct": 71.5,
    "false_accept_pct": 0.0,
    "recovered_ev": 1.8956
  },
  {
    "module": "rules",
    "verdict": "too strict",
    "false_reject_pct": 50.84,
    "false_accept_pct": 87.0,
    "recovered_ev": 1.9983
  },
  {
    "module": "dna",
    "verdict": "too strict",
    "false_reject_pct": 50.77,
    "false_accept_pct": 99.49,
    "recovered_ev": 1.942
  },
  {
    "module": "timeline",
    "verdict": "too strict",
    "false_reject_pct": 37.81,
    "false_accept_pct": 100.0,
    "recovered_ev": 2.3271
  },
  {
    "module": "direction",
    "verdict": "excellent",
    "false_reject_pct": 3.53,
    "false_accept_pct": 0.0,
    "recovered_ev": 3.6216
  },
  {
    "module": "fingerprint",
    "verdict": "too weak",
    "false_reject_pct": 0.0,
    "false_accept_pct": 100.0,
    "recovered_ev": null
  },
  {
    "module": "brain",
    "verdict": "excellent",
    "false_reject_pct": 0.29,
    "false_accept_pct": 0.0,
    "recovered_ev": 24.6205,
    "note": " best module"
  },
  {
    "module": "replay",
    "verdict": "excellent",
    "false_reject_pct": 0.0,
    "false_accept_pct": 0.0,
    "recovered_ev": null
  },
  {
    "module": "causality",
    "verdict": "excellent",
    "false_reject_pct": 0.0,
    "false_accept_pct": 0.0,
    "recovered_ev": null
  },
  {
    "module": "regime",
    "verdict": "excellent",
    "false_reject_pct": 0.0,
    "false_accept_pct": 0.0,
    "recovered_ev": null
  }
]
```
