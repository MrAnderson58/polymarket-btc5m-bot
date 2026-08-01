# EDGE_DISCOVERY_V3

_Market Edge Discovery V3 — combinatorial mining + Bayesian validation. Research only. Gate / Strategy / Optimizer / Paper / Execution unchanged._

## Run

- trades analyzed: **50**
- edges tested: **12143**
- combinatorial search space (est.): **43046656**
- screened / full-validated: 452 / 400
- surviving: **75** (READY=0 TEST=75)
- atoms / features: 30 / 8
- runtime: **2.184s**
- lake source: `research_lake_v1`

## Baseline

```json
{
  "n": 50,
  "pf": 0.43794751630699447,
  "expectancy": -25.2943,
  "winrate": 36.0
}
```

## TOP-20 statistically strongest edges

| # | score | n | EV | PF | WR | p | P(edge>0) | status | rule |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (direction==LONG)` |
| 2 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (regime==RANGE)` |
| 3 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (weekday==0.0)` |
| 4 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (hour in [18,24))` |
| 5 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RANGE)` |
| 6 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.0)` |
| 7 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (weekday==0.0)` |
| 8 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(direction==LONG) AND (regime==RANGE) AND (atr_pct<=q25(633.4)) AND (hour in [18` |
| 9 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(weekday==0.0) AND (atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RANG` |
| 10 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (direction==LONG)` |
| 11 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (direction==LONG)` |
| 12 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (regime==RANGE)` |
| 13 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (direction==LONG)` |
| 14 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (regime==RANGE)` |
| 15 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.0) AND (regime==RANG` |
| 16 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (direction==LONG) AND (regime==RANG` |
| 17 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (weekday==0.0) AND (regime==RANGE) AND (direction==LON` |
| 18 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (direction==LONG) AND (regime==R` |
| 19 | 79.07 | 14 | 49.337 | 8.5558 | 71.43 | 0.016393 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (regime==RANGE) AND (direction==` |
| 20 | 78.91 | 14 | 49.337 | 8.5558 | 71.43 | 0.032787 | 1.0 | TEST | `(atr_pct<=q25(633.4)) AND (regime==RANGE) AND (weekday==0.0) AND (direction==LON` |

## Interaction highlights

- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -22.9688] n=14 — `(atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04))`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -25.2943, -22.9688] n=14 — `(atr_pct<=median(4685)) AND (direction==LONG) AND (oi_delta<=median(-2.687e+04))`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -22.9688, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04)) AND (regime==RANGE)`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -22.9688, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04)) AND (weekday==0.0)`
- lift=2.74 joint_EV=-3.1568 alone=[-25.2943, -22.9688, -5.8968, -25.2943] n=14 — `(regime==RANGE) AND (oi_delta<=median(-2.687e+04)) AND (atr_pct<=median(4685)) AND (hour in [18,24))`
- lift=2.74 joint_EV=-3.1568 alone=[-25.2943, -5.8968, -25.2943, -22.9688] n=14 — `(direction==LONG) AND (atr_pct<=median(4685)) AND (weekday==0.0) AND (oi_delta<=median(-2.687e+04))`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -25.2943, -25.2943, -22.9688, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (weekday==0.0) AND (regime==RANGE) AND (oi_delta<=median(-2.687e+04)) AND (direction==LONG)`
- lift=2.74 joint_EV=-3.1568 alone=[-25.2943, -25.2943, -5.8968, -22.9688] n=14 — `(hour in [18,24)) AND (direction==LONG) AND (atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04))`
- lift=2.74 joint_EV=-3.1568 alone=[-22.9688, -5.8968, -25.2943, -25.2943] n=14 — `(oi_delta<=median(-2.687e+04)) AND (atr_pct<=median(4685)) AND (regime==RANGE) AND (direction==LONG)`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -22.9688, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04)) AND (direction==LONG)`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -25.2943, -22.9688, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (direction==LONG) AND (oi_delta<=median(-2.687e+04)) AND (regime==RANGE)`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -22.9688, -25.2943, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04)) AND (regime==RANGE) AND (direction==LONG)`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -22.9688, -25.2943, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04)) AND (weekday==0.0) AND (direction==LONG)`
- lift=2.74 joint_EV=-3.1568 alone=[-5.8968, -22.9688, -25.2943, -25.2943] n=14 — `(atr_pct<=median(4685)) AND (oi_delta<=median(-2.687e+04)) AND (weekday==0.0) AND (regime==RANGE)`
- lift=2.74 joint_EV=-3.1568 alone=[-25.2943, -22.9688, -5.8968, -25.2943, -25.2943] n=14 — `(regime==RANGE) AND (oi_delta<=median(-2.687e+04)) AND (atr_pct<=median(4685)) AND (hour in [18,24)) AND (direction==LONG)`

## Integrity

- gate_unchanged: True
- optimizer_unchanged: True
- strategy_unchanged: True
- paper_unchanged: True
- execution_unchanged: True
- no_n_plus_1_sql: True
