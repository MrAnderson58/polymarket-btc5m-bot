# PARETO_REPORT

Empirical 80% predictive-power prefix over ranked modules (by ΔEV / score).

- rule: 20% modules → 80% power (cumulative max(marginal_ev,0)+max(brain_hurt,0))
- pareto modules: **['causality', 'edge']**
- n_pareto / n_all: **2 / 9** (fraction=0.2222)
- power captured: **0.8895**

## Cumulative shares

| module | contrib | cum_share |
|---|---:|---:|
| `causality` | 17402.6771 | 0.547 |
| `edge` | 10894.7763 | 0.8895 |

## Keep / Remove

- keep: ['causality', 'edge', 'replay']
- remove: ['optimizer', 'features', 'validation', 'alpha', 'evolution', 'brain']
- estimated EV gain after simplification: **17059.5267**

## Ranked grades

```json
[
  {
    "module": "causality",
    "grade": "A+",
    "score": 75.0,
    "delta_ev": 27541.887,
    "marginal_ev": 15152.5077,
    "brain_hurt": 2250.1694,
    "f1": 1.0,
    "mutual_information": 0.0,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.2776,
    "never_takes": false,
    "keep": true,
    "remove": false
  },
  {
    "module": "edge",
    "grade": "A+",
    "score": 39.9711,
    "delta_ev": 23041.5482,
    "marginal_ev": 10894.7763,
    "brain_hurt": 0.0,
    "f1": 0.6241,
    "mutual_information": 0.99396,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.0284,
    "never_takes": false,
    "keep": true,
    "remove": false
  },
  {
    "module": "replay",
    "grade": "A+",
    "score": 33.3535,
    "delta_ev": 9024.4071,
    "marginal_ev": 1264.7133,
    "brain_hurt": 2250.1694,
    "f1": 0.0061,
    "mutual_information": 0.990774,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.4581,
    "never_takes": false,
    "keep": true,
    "remove": false
  },
  {
    "module": "optimizer",
    "grade": "REMOVE",
    "score": -5.0302,
    "delta_ev": 7759.6938,
    "marginal_ev": 0.0,
    "brain_hurt": 0.0,
    "f1": null,
    "mutual_information": 0.99396,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.4088,
    "never_takes": true,
    "keep": false,
    "remove": true
  },
  {
    "module": "features",
    "grade": "REMOVE",
    "score": -5.0302,
    "delta_ev": 7759.6938,
    "marginal_ev": 0.0,
    "brain_hurt": 0.0,
    "f1": null,
    "mutual_information": 0.99396,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.4088,
    "never_takes": true,
    "keep": false,
    "remove": true
  },
  {
    "module": "validation",
    "grade": "REMOVE",
    "score": -5.0302,
    "delta_ev": 7759.6938,
    "marginal_ev": 0.0,
    "brain_hurt": 0.0,
    "f1": null,
    "mutual_information": 0.99396,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.4088,
    "never_takes": true,
    "keep": false,
    "remove": true
  },
  {
    "module": "alpha",
    "grade": "REMOVE",
    "score": -10.0302,
    "delta_ev": 7759.6938,
    "marginal_ev": 0.0,
    "brain_hurt": 0.0,
    "f1": null,
    "mutual_information": 0.99396,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.4088,
    "never_takes": true,
    "keep": false,
    "remove": true
  },
  {
    "module": "evolution",
    "grade": "REMOVE",
    "score": -16.6113,
    "delta_ev": 7759.6938,
    "marginal_ev": -4387.0781,
    "brain_hurt": 0.0,
    "f1": null,
    "mutual_information": 0.99396,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.4088,
    "never_takes": true,
    "keep": false,
    "remove": true
  },
  {
    "module": "brain",
    "grade": "REMOVE",
    "score": -28.4213,
    "delta_ev": 10009.8632,
    "marginal_ev": -12672.4486,
    "brain_hurt": 0.0,
    "f1": 0.0062,
    "mutual_information": 0.99396,
    "hurt_if_removed": 0.0,
    "mean_redundancy": 0.4613,
    "never_takes": false,
    "keep": false,
    "remove": true
  }
]
```
