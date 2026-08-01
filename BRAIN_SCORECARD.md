# BRAIN_SCORECARD

Shadow Live Evaluation — Brain vs Production.

## Headline

- Brain hit rate: **0.5457**
- Production hit rate: **0.1326**
- ΔWR: **0.4131**
- ΔEV: **10009.8632**
- ΔPF: **None**
- False positives: **0**
- False negatives: **8689**
- Brain wins / Production wins / Ties: **16630** / **2510** / **20**

## Runtime

```json
{
  "mean_decision_ms": 0.06,
  "p95_decision_ms": 0.067,
  "budget_ms": 1000,
  "within_budget": true
}
```

## Rolling

```json
{
  "last_50": {
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
  },
  "last_100": {
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
  },
  "last_500": {
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
  },
  "last_1000": {
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
}
```

## Confidence reliability

```json
{
  "n_bins": 0,
  "mean_gap": null,
  "reliable_bins": 0,
  "reliability_score": 0.0
}
```

## Confidence curve

| confidence | n | actual WR | reliable |
|---|---|---|---|
| 0.55 | 0 | None | False |
| 0.6 | 0 | None | False |
| 0.65 | 0 | None | False |
| 0.7 | 0 | None | False |
| 0.75 | 0 | None | False |
| 0.8 | 0 | None | False |
| 0.85 | 0 | None | False |
| 0.9 | 0 | None | False |
| 0.95 | 0 | None | False |
