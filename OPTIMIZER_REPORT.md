# OPTIMIZER_REPORT — Adaptive Strategy Optimizer V1

**Generated:** 1785433809
**Sample size:** 50
**Confidence:** 0.6
**Auto-apply eligible:** False
**Actions:** RECOMMEND_ONLY

## Current parameters

```json
{
  "disabled_symbols": [],
  "confidence_threshold": null,
  "exploration_rate": 0.1,
  "S57_EXPLORATION_RATE_env": null
}
```

## Recommended parameters

```json
{
  "disabled_symbols": [],
  "confidence_threshold": null,
  "exploration_rate": 0.1,
  "exits": [
    {
      "param": "trailing_activation_pct",
      "recommended": 0.872,
      "rationale": "avg MFE=1.9386; activate trail near ~45\u201370% of typical MFE",
      "evidence_n": 50
    },
    {
      "param": "tp1_pct",
      "recommended": 1.066,
      "rationale": "align TP1 with ~55% of avg MFE (1.9386)",
      "evidence_n": 50
    },
    {
      "param": "tp2_pct",
      "recommended": 1.648,
      "rationale": "align TP2 with ~85% of avg MFE (1.9386)",
      "evidence_n": 50
    },
    {
      "param": "stop_pct",
      "recommended": -2.363,
      "rationale": "avg MAE=-2.2502; stop slightly beyond typical adverse excursion",
      "evidence_n": 50
    },
    {
      "param": "stop_management",
      "recommended": "review_stop_distance",
      "rationale": "STOP exits n=12 expectancy=-145.0731",
      "evidence_n": 12
    }
  ]
}
```

## Expected improvement

```json
{
  "expectancy_lift": null,
  "pf_lift": null,
  "note": "Lift vs baseline if confidence filter applied; exits are recommend-only"
}
```

## Safety thresholds

```json
{
  "OPT_AUTO_APPLY": false,
  "OPT_AUTO_MIN_SAMPLE": 50,
  "OPT_AUTO_MIN_CONFIDENCE": 0.7,
  "OPT_AUTO_MIN_PF_IMPROVEMENT": 0.1,
  "OPT_MIN_SAMPLE": 20
}
```

## Overall

n=50 WR=36.0% E=-25.2943 PF=0.4379 avg_pnl%=-1.2647 hold_s=37183.0 MFE=1.9386 MAE=-2.2502

## By symbol

- **BTC**: n=3 WR=100.0% E=37.528 PF=inf avg_pnl%=1.8764 hold_s=83715.0 MFE=3.2926 MAE=0.0
- **DOGE**: n=3 WR=0.0% E=-28.0167 PF=0.0 avg_pnl%=-1.4008 hold_s=62109.7 MFE=0.0 MAE=-1.4008
- **ETH**: n=3 WR=100.0% E=118.7946 PF=inf avg_pnl%=5.9397 hold_s=94733.0 MFE=8.6856 MAE=0.0
- **INJ**: n=3 WR=0.0% E=-81.812 PF=0.0 avg_pnl%=-4.0906 hold_s=62008.7 MFE=0.0 MAE=-4.0906
- **LINK**: n=3 WR=100.0% E=101.005 PF=inf avg_pnl%=5.0502 hold_s=93566.3 MFE=7.795 MAE=0.0
- **AVAX**: n=3 WR=0.0% E=-29.3074 PF=0.0 avg_pnl%=-1.4654 hold_s=32655.0 MFE=1.9898 MAE=-1.4654
- **BNB**: n=3 WR=0.0% E=-14.0076 PF=0.0 avg_pnl%=-0.7004 hold_s=32655.0 MFE=1.055 MAE=-0.7004
- **APT**: n=3 WR=66.67% E=2.7815 PF=1.3333 avg_pnl%=0.139 hold_s=12377.7 MFE=1.6022 MAE=-0.4172
- **ARB**: n=3 WR=0.0% E=-215.2443 PF=0.0 avg_pnl%=-10.7622 hold_s=648.7 MFE=0.0 MAE=-10.7622
- **ADA**: n=3 WR=66.67% E=1.6835 PF=inf avg_pnl%=0.0842 hold_s=938.7 MFE=0.4209 MAE=0.0
- **TON**: n=2 WR=0.0% E=0.0 PF=0.0 avg_pnl%=0.0 hold_s=86410.0 MFE=0.0 MAE=0.0
- **MATIC**: n=2 WR=0.0% E=0.0 PF=0.0 avg_pnl%=0.0 hold_s=44324.5 MFE=0.0 MAE=0.0
- **MANTA**: n=2 WR=100.0% E=53.7355 PF=inf avg_pnl%=2.6868 hold_s=32655.0 MFE=4.5242 MAE=0.0
- **XRP**: n=2 WR=0.0% E=-20.9895 PF=0.0 avg_pnl%=-1.0495 hold_s=32655.0 MFE=2.5487 MAE=-1.0495
- **SOL**: n=2 WR=50.0% E=-19.6207 PF=0.2055 avg_pnl%=-0.981 hold_s=17178.5 MFE=1.3824 MAE=-1.2348
- **PEPE**: n=2 WR=100.0% E=28.7115 PF=inf avg_pnl%=1.4356 hold_s=2239.0 MFE=2.7486 MAE=0.0
- **WIF**: n=2 WR=0.0% E=-22.9455 PF=0.0 avg_pnl%=-1.1472 hold_s=427.0 MFE=0.0 MAE=-1.1472
- **SUI**: n=2 WR=0.0% E=-77.1863 PF=0.0 avg_pnl%=-3.8594 hold_s=261.5 MFE=0.0 MAE=-3.8594
- **OP**: n=2 WR=0.0% E=-179.8202 PF=0.0 avg_pnl%=-8.991 hold_s=217.5 MFE=0.0 MAE=-8.991
- **NEAR**: n=2 WR=0.0% E=-234.3485 PF=0.0 avg_pnl%=-11.7174 hold_s=95.5 MFE=0.0 MAE=-11.7174

## By direction

- **LONG**: n=50 WR=36.0% E=-25.2943 PF=0.4379 avg_pnl%=-1.2647 hold_s=37183.0 MFE=1.9386 MAE=-2.2502

## By gate_decision

- **INSUFFICIENT_HISTORY**: n=45 WR=33.33% E=-28.3874 PF=0.3677 avg_pnl%=-1.4194 hold_s=20666.1 MFE=1.8845 MAE=-2.2448
- **REGIME_EXPLORE**: n=5 WR=60.0% E=2.5435 PF=1.0553 avg_pnl%=0.1272 hold_s=185835.0 MFE=2.4261 MAE=-2.2989

## By pattern

- **{"candidate_state": "provisional", "reje**: n=50 WR=36.0% E=-25.2943 PF=0.4379 avg_pnl%=-1.2647 hold_s=37183.0 MFE=1.9386 MAE=-2.2502

## By news_category

- **NULL**: n=50 WR=36.0% E=-25.2943 PF=0.4379 avg_pnl%=-1.2647 hold_s=37183.0 MFE=1.9386 MAE=-2.2502

## By confidence bucket

- **NULL**: n=50 WR=36.0% E=-25.2943 PF=0.4379 avg_pnl%=-1.2647 hold_s=37183.0 MFE=1.9386 MAE=-2.2502

## By market regime

- **RANGE**: n=50 WR=36.0% E=-25.2943 PF=0.4379 avg_pnl%=-1.2647 hold_s=37183.0 MFE=1.9386 MAE=-2.2502

## Symbol filter decisions

- INSUFFICIENT_SAMPLE: BTC n=3 PF=None E=37.528
- INSUFFICIENT_SAMPLE: DOGE n=3 PF=0.0 E=-28.0167
- INSUFFICIENT_SAMPLE: ETH n=3 PF=None E=118.7946
- INSUFFICIENT_SAMPLE: INJ n=3 PF=0.0 E=-81.812
- INSUFFICIENT_SAMPLE: LINK n=3 PF=None E=101.005
- INSUFFICIENT_SAMPLE: AVAX n=3 PF=0.0 E=-29.3074
- INSUFFICIENT_SAMPLE: BNB n=3 PF=0.0 E=-14.0076
- INSUFFICIENT_SAMPLE: APT n=3 PF=1.3333 E=2.7815
- INSUFFICIENT_SAMPLE: ARB n=3 PF=0.0 E=-215.2443
- INSUFFICIENT_SAMPLE: ADA n=3 PF=None E=1.6835
- INSUFFICIENT_SAMPLE: TON n=2 PF=0.0 E=0.0
- INSUFFICIENT_SAMPLE: MATIC n=2 PF=0.0 E=0.0
- INSUFFICIENT_SAMPLE: MANTA n=2 PF=None E=53.7355
- INSUFFICIENT_SAMPLE: XRP n=2 PF=0.0 E=-20.9895
- INSUFFICIENT_SAMPLE: SOL n=2 PF=0.2055 E=-19.6207
- INSUFFICIENT_SAMPLE: PEPE n=2 PF=None E=28.7115
- INSUFFICIENT_SAMPLE: WIF n=2 PF=0.0 E=-22.9455
- INSUFFICIENT_SAMPLE: SUI n=2 PF=0.0 E=-77.1863
- INSUFFICIENT_SAMPLE: OP n=2 PF=0.0 E=-179.8202
- INSUFFICIENT_SAMPLE: NEAR n=2 PF=0.0 E=-234.3485

## Confidence optimization

Recommended threshold: **None**

- thr=0.5: n=0
- thr=0.55: n=0
- thr=0.6: n=0
- thr=0.65: n=0
- thr=0.7: n=0
- thr=0.75: n=0
- thr=0.8: n=0
- thr=0.85: n=0
- thr=0.9: n=0
- thr=0.95: n=0

## Exploration rate

```json
{
  "exploration_rate": 0.1,
  "base": 0.1,
  "min": 0.02,
  "max": 0.25,
  "inputs": {
    "n": 50,
    "pf": 0.4379,
    "pf_inf": false,
    "volatility_pnl_pct": 4.5919,
    "max_drawdown_pnl_pct": 90.3329
  }
}
```

## Exit recommendations (no auto-apply)

- **trailing_activation_pct** → `0.872` — avg MFE=1.9386; activate trail near ~45–70% of typical MFE (n=50)
- **tp1_pct** → `1.066` — align TP1 with ~55% of avg MFE (1.9386) (n=50)
- **tp2_pct** → `1.648` — align TP2 with ~85% of avg MFE (1.9386) (n=50)
- **stop_pct** → `-2.363` — avg MAE=-2.2502; stop slightly beyond typical adverse excursion (n=50)
- **stop_management** → `review_stop_distance` — STOP exits n=12 expectancy=-145.0731 (n=12)

## Evidence

Source: closed `market_events_paper_trades_s42` (+ S55), fallback S56 lab trades.
