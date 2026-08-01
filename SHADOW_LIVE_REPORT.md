# SHADOW_LIVE_REPORT

_Shadow Live Evaluation V1 — research-only. No trade execution. Does not modify Paper / Execution / Gate / Strategy / Optimizer._

## Run

- candidates: **50**
- shadow decisions: **50**
- evaluated (closed): **50**
- runtime total: **0.026s**
- mean decision latency: **0.104 ms**
- p95 decision latency: **0.125 ms**
- lake source: `research_lake_v1`

## Production vs Brain (all)

```json
{
  "n": 50,
  "brain_hit_rate": 0.64,
  "production_hit_rate": 0.66,
  "delta_wr": -0.02,
  "brain_ev": 2250.1694,
  "production_ev": 12.7177,
  "delta_ev": 2237.4517,
  "brain_pf": null,
  "production_pf": 1.0553,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 3,
  "brain_wins": 27,
  "production_wins": 3,
  "ties": 20
}
```

## Rolling windows

### last_50

```json
{
  "n": 50,
  "brain_hit_rate": 0.64,
  "production_hit_rate": 0.66,
  "delta_wr": -0.02,
  "brain_ev": 2250.1694,
  "production_ev": 12.7177,
  "delta_ev": 2237.4517,
  "brain_pf": null,
  "production_pf": 1.0553,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 3,
  "brain_wins": 27,
  "production_wins": 3,
  "ties": 20
}
```

### last_100

```json
{
  "n": 50,
  "brain_hit_rate": 0.64,
  "production_hit_rate": 0.66,
  "delta_wr": -0.02,
  "brain_ev": 2250.1694,
  "production_ev": 12.7177,
  "delta_ev": 2237.4517,
  "brain_pf": null,
  "production_pf": 1.0553,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 3,
  "brain_wins": 27,
  "production_wins": 3,
  "ties": 20
}
```

### last_500

```json
{
  "n": 50,
  "brain_hit_rate": 0.64,
  "production_hit_rate": 0.66,
  "delta_wr": -0.02,
  "brain_ev": 2250.1694,
  "production_ev": 12.7177,
  "delta_ev": 2237.4517,
  "brain_pf": null,
  "production_pf": 1.0553,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 3,
  "brain_wins": 27,
  "production_wins": 3,
  "ties": 20
}
```

### last_1000

```json
{
  "n": 50,
  "brain_hit_rate": 0.64,
  "production_hit_rate": 0.66,
  "delta_wr": -0.02,
  "brain_ev": 2250.1694,
  "production_ev": 12.7177,
  "delta_ev": 2237.4517,
  "brain_pf": null,
  "production_pf": 1.0553,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 3,
  "brain_wins": 27,
  "production_wins": 3,
  "ties": 20
}
```

## Confidence calibration curve

```json
[
  {
    "confidence": 0.55,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.6,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.65,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.7,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.75,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.8,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.85,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.9,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  },
  {
    "confidence": 0.95,
    "n": 0,
    "actual_wr": null,
    "reliable": false
  }
]
```

## Calibration summary

```json
{
  "n_bins": 0,
  "mean_gap": null,
  "reliable_bins": 0,
  "reliability_score": 0.0
}
```

## Promotion

```json
{
  "status": "NOT_READY",
  "recommendation": "Brain is NOT ready \u2014 shadow shows no clear edge yet.",
  "ready_for_paper_ab": false,
  "auto_promotion": false,
  "reasons": [
    "last_100: \u0394EV=2237.4517 positive but \u0394WR=-0.02 not yet \u22650"
  ],
  "n": 50
}
```

## Integrity

- no_execution: True
- gate_unchanged: True
- strategy_unchanged: True
- paper_unchanged: True
- execution_unchanged: True
- optimizer_unchanged: True
- research_only: True
