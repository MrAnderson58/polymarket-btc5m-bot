# ML_REPORT — Feature Store & ML Ranking V1 (Shadow)

**Generated:** 1785435030
**Shadow mode:** True
**ML may execute:** False
**Backend:** sklearn
**Feature version:** v1

## Dataset

- size: **50** (train=35 test=15)
- feature count: **30**
- class balance: `{"profitable": 18, "unprofitable": 32, "positive_rate": 0.36}`

## Metrics (test / holdout)

```json
{
  "roc_auc": 0.8929,
  "precision": 1.0,
  "recall": 0.1429,
  "brier": 0.3356,
  "calibration_curve": [
    {
      "bin": "0.00-0.20",
      "mean_predicted": 0.0725,
      "frac_positive": 0.4286,
      "n": 14
    },
    {
      "bin": "0.80-1.00",
      "mean_predicted": 0.8375,
      "frac_positive": 1.0,
      "n": 1
    }
  ]
}
```

## Metrics (train)

```json
{
  "roc_auc": 1.0,
  "precision": 1.0,
  "recall": 1.0,
  "brier": 0.0138,
  "calibration_curve": [
    {
      "bin": "0.00-0.20",
      "mean_predicted": 0.0606,
      "frac_positive": 0.0,
      "n": 23
    },
    {
      "bin": "0.20-0.40",
      "mean_predicted": 0.2816,
      "frac_positive": 0.0,
      "n": 1
    },
    {
      "bin": "0.60-0.80",
      "mean_predicted": 0.7642,
      "frac_positive": 1.0,
      "n": 4
    },
    {
      "bin": "0.80-1.00",
      "mean_predicted": 0.896,
      "frac_positive": 1.0,
      "n": 7
    }
  ]
}
```

## Feature importance (top positive)

- **cat__symbol**: 0.858932
- **oi_delta**: 0.141068
- **cat__direction**: 0.0
- **cat__pattern**: 0.0
- **cat__gate_decision**: 0.0
- **cat__news_category**: 0.0
- **cat__market_regime**: 0.0
- **confidence**: 0.0
- **ai_score**: 0.0
- **macro_score**: 0.0

## Feature importance (lowest / negative end)

- **weekday**: 0.0
- **hour**: 0.0
- **trend**: 0.0
- **fear_greed**: 0.0
- **volume**: 0.0
- **btc_move**: 0.0
- **time_to_expiry**: 0.0
- **book_imbalance**: 0.0
- **spread**: 0.0
- **liquidation_metric**: 0.0

## SHAP summary

SHAP available: **False**

- cat__symbol: mean_abs=0.434286 (permutation_importance)
- oi_delta: mean_abs=0.085714 (permutation_importance)
- cat__direction: mean_abs=0.0 (permutation_importance)
- cat__pattern: mean_abs=0.0 (permutation_importance)
- cat__gate_decision: mean_abs=0.0 (permutation_importance)
- cat__news_category: mean_abs=0.0 (permutation_importance)
- cat__market_regime: mean_abs=0.0 (permutation_importance)
- confidence: mean_abs=0.0 (permutation_importance)
- ai_score: mean_abs=0.0 (permutation_importance)
- macro_score: mean_abs=0.0 (permutation_importance)
- news_score: mean_abs=0.0 (permutation_importance)
- volatility: mean_abs=0.0 (permutation_importance)
- atr: mean_abs=0.0 (permutation_importance)
- vwap_distance: mean_abs=0.0 (permutation_importance)
- ema20_distance: mean_abs=0.0 (permutation_importance)

## Recommended future features

- realized_volatility_regime_interaction
- orderbook_depth_imbalance_at_entry
- funding_cross_exchange_dispersion
- news_embedding_similarity_to_past_winners
- time_of_day_session_flags
- btc_lead_lag_vs_alt_beta

## Safety

- Existing strategy remains authoritative.
- This model only emits shadow `ml_score` values.
- It must never place or block trades in V1.
