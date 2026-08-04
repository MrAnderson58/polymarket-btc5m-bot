# REGIME_TRANSITION_REPORT

_Market Regime Transition Engine V1 — research only._

- n: 19205
- runtime: **64.974s**
- markov transitions: 19204

## Current / Prediction
```
{
  "current": "RANGE",
  "predictions": [
    {
      "state": "RANGE",
      "prob_pct": 57.6
    },
    {
      "state": "WEAK_BEAR",
      "prob_pct": 30.3
    },
    {
      "state": "WEAK_BULL",
      "prob_pct": 12.0
    }
  ],
  "top": "RANGE",
  "top_prob_pct": 57.6
}
```

## Recommendation
```
{
  "current_regime": "RANGE",
  "transition_probability": 0.576,
  "next_state": "RANGE",
  "confidence": 0.576,
  "historical_wr": 100.0,
  "historical_pf": null,
  "historical_ev": 2.0145,
  "recommended_bias": "NO TRADE",
  "recommendation": "RESEARCH ONLY",
  "research_only": true
}
```

## Top profitable

- `lb10:pat:WWWBWWWBWB` n=36 WR=91.67 EV=4.627 ready=True p=0.02649
- `lb10:pat:BBWWWBWWWB` n=30 WR=73.33 EV=4.5361 ready=True p=0.013245
- `lb10:pat:WWWWWWBWWW` n=43 WR=88.37 EV=3.4314 ready=True p=0.033113
- `lb10:pat:BWBBWWWWWB` n=21 WR=90.48 EV=3.2721 ready=True p=0.013245
- `lb10:pat:WWBWWWWWBB` n=32 WR=84.38 EV=3.413 ready=True p=0.006623
- `lb10:pat:WWWWWWWBWW` n=21 WR=90.48 EV=3.3017 ready=True p=0.013245
- `lb10:pat:WWBWWWWWWB` n=25 WR=96.0 EV=3.2702 ready=True p=0.05298
- `lb10:pat:WBBBBBBBBW` n=72 WR=70.83 EV=3.3761 ready=True p=0.013245
- `lb10:pat:BWWWWWBBWW` n=29 WR=82.76 EV=2.8366 ready=True p=0.046358
- `lb10:pat:WWWWWWWWWW` n=58 WR=72.41 EV=3.0794 ready=True p=0.02649
- `lb10:pat:BWWBWWWWWB` n=26 WR=84.62 EV=2.7048 ready=True p=0.02649
- `lb10:pat:BWWWWWBWWW` n=47 WR=72.34 EV=2.65 ready=True p=0.006623
- `lb10:pat:WWWWWBWWWW` n=72 WR=90.28 EV=2.2696 ready=True p=0.066225
- `lb10:pat:BWWWWWWBWW` n=29 WR=82.76 EV=2.5032 ready=True p=0.059603
- `lb10:pat:WWWWWWWWWB` n=20 WR=75.0 EV=2.6897 ready=True p=0.046358
- `lb10:pat:BWWWWWBWWB` n=59 WR=86.44 EV=2.2141 ready=True p=0.039735
- `lb10:pat:WWWWBWWBWB` n=24 WR=62.5 EV=2.5963 ready=True p=0.046358
- `lb1:WEAK_BULL→WEAK_BEAR` n=665 WR=100.0 EV=2.2376 ready=True p=0.006623
- `lb3:WEAK_BULL→WEAK_BEAR` n=665 WR=100.0 EV=2.2376 ready=True p=0.006623
- `lb5:WEAK_BULL→WEAK_BEAR` n=665 WR=100.0 EV=2.2376 ready=True p=0.006623
- `lb10:WEAK_BULL→WEAK_BEAR` n=665 WR=100.0 EV=2.2376 ready=True p=0.006623
- `lb20:WEAK_BULL→WEAK_BEAR` n=665 WR=100.0 EV=2.2376 ready=True p=0.006623
- `lb50:WEAK_BULL→WEAK_BEAR` n=665 WR=100.0 EV=2.2376 ready=True p=0.006623
- `lb10:pat:WWWWBBWWWW` n=30 WR=93.33 EV=1.9848 ready=True p=0.05298
- `lb10:pat:WBBWBWBBBB` n=20 WR=75.0 EV=2.3105 ready=True p=0.046358

## Top dangerous

- `lb3:pat:LLL` n=30 WR=23.33 EV=-34.8641
- `lb5:pat:LLLLL` n=15 WR=33.33 EV=-24.387
- `lb10:pat:WBWWWWWBBW` n=36 WR=72.22 EV=-5.3265
- `lb10:pat:BWBWBBBBWW` n=20 WR=45.0 EV=-3.0899
- `lb20:pat:BBBBBBBWWWBBBBWBBBWB` n=20 WR=5.0 EV=-1.4612
- `lb1:RANGE→RANGE` n=6063 WR=0.53 EV=-0.2372
- `lb3:RANGE→RANGE` n=6063 WR=0.53 EV=-0.2372
- `lb5:RANGE→RANGE` n=6063 WR=0.53 EV=-0.2372
- `lb10:RANGE→RANGE` n=6063 WR=0.53 EV=-0.2372
- `lb20:RANGE→RANGE` n=6063 WR=0.53 EV=-0.2372
- `lb50:RANGE→RANGE` n=6063 WR=0.53 EV=-0.2372
- `lb1:WEAK_BULL→RANGE` n=1317 WR=0.15 EV=-0.1571
- `lb3:WEAK_BULL→RANGE` n=1317 WR=0.15 EV=-0.1571
- `lb5:WEAK_BULL→RANGE` n=1317 WR=0.15 EV=-0.1571
- `lb10:WEAK_BULL→RANGE` n=1317 WR=0.15 EV=-0.1571
