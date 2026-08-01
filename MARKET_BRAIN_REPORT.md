# MARKET_BRAIN_REPORT

_Adaptive Market Brain V1 — unified research opinion layer. Does not modify Paper / Execution / Strategy / Gate._

## Run

- trades analyzed: **50**
- decisions: **50**
- runtime: **80.578s**
- NO_TRADE (conflicts): **18**
- lake source: `research_lake_v1`

## Module weights

- `lake`: 0.08
- `replay`: 0.2
- `edge`: 0.18
- `alpha`: 0.12
- `causality`: 0.15
- `optimizer`: 0.1
- `validation`: 0.08
- `feature_store`: 0.05
- `experiments`: 0.04

## Agreement matrix (mean pairwise)

```json
{
  "lake": {
    "lake": 1.0,
    "replay": 1.0,
    "edge": -1.0,
    "alpha": 0.0,
    "causality": 0.18,
    "optimizer": 0.0,
    "validation": 0.0,
    "feature_store": 0.0,
    "experiments": 0.0
  },
  "replay": {
    "lake": 1.0,
    "replay": 1.0,
    "edge": -1.0,
    "alpha": 0.0,
    "causality": 0.18,
    "optimizer": 0.0,
    "validation": 0.0,
    "feature_store": 0.0,
    "experiments": 0.0
  },
  "edge": {
    "lake": -1.0,
    "replay": -1.0,
    "edge": 1.0,
    "alpha": 0.0,
    "causality": -0.18,
    "optimizer": 0.0,
    "validation": 0.0,
    "feature_store": 0.0,
    "experiments": 0.0
  },
  "alpha": {
    "lake": 0.0,
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 1.0,
    "causality": 0.0,
    "optimizer": 0.0,
    "validation": 0.0,
    "feature_store": 0.0,
    "experiments": 0.0
  },
  "causality": {
    "lake": 0.18,
    "replay": 0.18,
    "edge": -0.18,
    "alpha": 0.0,
    "causality": 1.0,
    "optimizer": 0.0,
    "validation": 0.0,
    "feature_store": 0.0,
    "experiments": 0.0
  },
  "optimizer": {
    "lake": 0.0,
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "causality": 0.0,
    "optimizer": 1.0,
    "validation": 0.0,
    "feature_store": 0.0,
    "experiments": 0.0
  },
  "validation": {
    "lake": 0.0,
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "causality": 0.0,
    "optimizer": 0.0,
    "validation": 1.0,
    "feature_store": 0.0,
    "experiments": 0.0
  },
  "feature_store": {
    "lake": 0.0,
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "causality": 0.0,
    "optimizer": 0.0,
    "validation": 0.0,
    "feature_store": 1.0,
    "experiments": 0.0
  },
  "experiments": {
    "lake": 0.0,
    "replay": 0.0,
    "edge": 0.0,
    "alpha": 0.0,
    "causality": 0.0,
    "optimizer": 0.0,
    "validation": 0.0,
    "feature_store": 0.0,
    "experiments": 1.0
  }
}
```

## Conflict statistics

```json
{
  "n_conflicts_logged": 18,
  "n_no_trade": 18,
  "conflict_rate": 0.36,
  "levels": {
    "HIGH": 18
  }
}
```

## Calibrated confidence

- mean raw: 0.1577
- mean calibrated: 0.2791
- calibration bins: 10

## Historical replay performance (Brain vs modules)

```json
{
  "brain": {
    "n": 32,
    "hit_rate": 1.0
  },
  "modules": {
    "lake": {
      "n": 50,
      "hit_rate": 0.54
    },
    "replay": {
      "n": 50,
      "hit_rate": 0.54
    },
    "edge": {
      "n": 50,
      "hit_rate": 0.36
    },
    "alpha": {
      "n": 50,
      "hit_rate": 0.1
    },
    "causality": {
      "n": 50,
      "hit_rate": 1.0
    },
    "optimizer": {
      "n": 50,
      "hit_rate": 0.1
    },
    "validation": {
      "n": 50,
      "hit_rate": 0.1
    },
    "feature_store": {
      "n": 50,
      "hit_rate": 0.1
    },
    "experiments": {
      "n": 50,
      "hit_rate": 0.1
    }
  }
}
```

## Integrity

- gate_unchanged: True
- strategy_unchanged: True
- paper_unchanged: True
- execution_unchanged: True
- research_only: True
