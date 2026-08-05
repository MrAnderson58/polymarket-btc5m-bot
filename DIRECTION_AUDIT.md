# DIRECTION_AUDIT

- cause: **market_or_decision_book**
- SHORT dominance elite=98.13% tracks Decision accepted SHORT=98.13% (corpus=49.71%). Profile inherits Book B accept mix — not an independent profile bug.

```json
{
  "corpus": {
    "LONG": {
      "key": "LONG",
      "n": 9658,
      "pct": 50.29,
      "wr": 26.14,
      "pf": 2.3876,
      "ev": 0.3233
    },
    "SHORT": {
      "key": "SHORT",
      "n": 9547,
      "pct": 49.71,
      "wr": 64.89,
      "pf": 15.09,
      "ev": 1.2278
    }
  },
  "decision_accepted": {
    "SHORT": {
      "key": "SHORT",
      "n": 4034,
      "pct": 98.13,
      "wr": 76.57,
      "pf": null,
      "ev": 1.5502
    },
    "LONG": {
      "key": "LONG",
      "n": 77,
      "pct": 1.87,
      "wr": 58.44,
      "pf": null,
      "ev": 0.6736
    }
  },
  "elite": {
    "SHORT": {
      "key": "SHORT",
      "n": 4034,
      "pct": 98.13,
      "wr": 76.57,
      "pf": null,
      "ev": 1.5502
    },
    "LONG": {
      "key": "LONG",
      "n": 77,
      "pct": 1.87,
      "wr": 58.44,
      "pf": null,
      "ev": 0.6736
    }
  },
  "cause": "market_or_decision_book",
  "explain": "SHORT dominance elite=98.13% tracks Decision accepted SHORT=98.13% (corpus=49.71%). Profile inherits Book B accept mix \u2014 not an independent profile bug."
}
```
