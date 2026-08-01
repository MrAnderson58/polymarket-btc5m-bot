# SHADOW_LIVE_REPORT

_Shadow Live Evaluation V1 — research-only. No trade execution. Does not modify Paper / Execution / Gate / Strategy / Optimizer._

## Run

- candidates: **19160**
- shadow decisions: **19160**
- evaluated (closed): **19160**
- runtime total: **66.761s**
- mean decision latency: **0.06 ms**
- p95 decision latency: **0.067 ms**
- lake source: `research_lake`

## Production vs Brain (all)

```json
{
  "n": 19160,
  "brain_hit_rate": 0.5457,
  "production_hit_rate": 0.1326,
  "delta_wr": 0.4131,
  "brain_ev": 2250.1694,
  "production_ev": -7759.6938,
  "delta_ev": 10009.8632,
  "brain_pf": null,
  "production_pf": 0.3737,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 8689,
  "brain_wins": 16630,
  "production_wins": 2510,
  "ties": 20
}
```

## Rolling windows

### last_50

```json
{
  "n": 50,
  "brain_hit_rate": 0.22,
  "production_hit_rate": 0.08,
  "delta_wr": 0.14,
  "brain_ev": 0.0,
  "production_ev": -65.6379,
  "delta_ev": 65.6379,
  "brain_pf": null,
  "production_pf": 0.0668,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 39,
  "brain_wins": 46,
  "production_wins": 4,
  "ties": 0
}
```

### last_100

```json
{
  "n": 100,
  "brain_hit_rate": 0.23,
  "production_hit_rate": 0.07,
  "delta_wr": 0.16,
  "brain_ev": 0.0,
  "production_ev": -132.4798,
  "delta_ev": 132.4798,
  "brain_pf": null,
  "production_pf": 0.0526,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 77,
  "brain_wins": 93,
  "production_wins": 7,
  "ties": 0
}
```

### last_500

```json
{
  "n": 500,
  "brain_hit_rate": 0.298,
  "production_hit_rate": 0.07,
  "delta_wr": 0.228,
  "brain_ev": 0.0,
  "production_ev": -662.6005,
  "delta_ev": 662.6005,
  "brain_pf": null,
  "production_pf": 0.0543,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 351,
  "brain_wins": 465,
  "production_wins": 35,
  "ties": 0
}
```

### last_1000

```json
{
  "n": 1000,
  "brain_hit_rate": 0.347,
  "production_hit_rate": 0.078,
  "delta_wr": 0.269,
  "brain_ev": 0.0,
  "production_ev": -1301.967,
  "delta_ev": 1301.967,
  "brain_pf": null,
  "production_pf": 0.058,
  "delta_pf": null,
  "false_positives": 0,
  "false_negatives": 653,
  "brain_wins": 922,
  "production_wins": 78,
  "ties": 0
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
  "status": "BEATS_PRODUCTION",
  "recommendation": "Brain beats production \u2014 improve calibration / sample size before paper A/B.",
  "ready_for_paper_ab": false,
  "auto_promotion": false,
  "reasons": [
    "last_100: brain \u0394EV>0 and hit-rate \u2265 production",
    "last_500: brain beats production on \u0394EV/WR",
    "calibration not reliable enough (score=0.0, mean_gap=None)"
  ],
  "n": 19160
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
