# SIGNAL_EVOLUTION_REPORT

_Signal Evolution Engine V1 — research-only. Does not modify Paper / Strategy / Execution / Gate / Optimizer._

## Run

- tracked signals: **77**
- occurrences: **1035**
- runtime: **0.122s**
- mean update latency: **0.436 ms**
- source: `s40+s42+lake`

## Strongest signals

| signal | score | status | half_life | drift | confidence |
|---|---|---|---|---|---|
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG)` | 100.0 | BIRTH | None | 0.0 | 0.7675 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (gate_decision==INSUFFICIENT_HISTORY)` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (gate_decision==INSUFFICIENT_HISTORY) AND (regime==RANGE)` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RANGE)` | 100.0 | BIRTH | None | 0.0 | 0.7675 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RANGE) AND (gate_decision==INSUFFICIENT_HISTORY)` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.0)` | 100.0 | BIRTH | None | 0.0 | 0.7675 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.0) AND (gate_decision==INSUFFICIENT_HISTORY)` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.0) AND (gate_decision==INSUFFICIENT_HISTORY) AND (regime==RA` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.0) AND (regime==RANGE)` | 100.0 | BIRTH | None | 0.0 | 0.7675 |
| `edge:(atr_pct<=q25(633.4)) AND (direction==LONG) AND (weekday==0.0) AND (regime==RANGE) AND (gate_decision==INSUFFICIENT_HIST` | 100.0 | BIRTH | None | 0.0 | 0.7637 |

## Weakest signals

| signal | score | status | half_life | drift | confidence |
|---|---|---|---|---|---|
| `lake:regime:RANGE|LONG` | 0.0 | DECAY | None | 1.0 | 0.2605 |
| `g31_family:LONG` | 0.0 | DECAY | None | 1.0 | 0.2605 |
| `edge:(weekday==0.0) AND (atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RANGE) AND (gate_decision==INSUFFICIENT_HIST` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(weekday==0.0) AND (atr_pct<=q25(633.4)) AND (direction==LONG) AND (regime==RANGE)` | 100.0 | BIRTH | None | 0.0 | 0.7675 |
| `edge:(regime==RANGE) AND (weekday==0.0) AND (atr_pct<=q25(633.4)) AND (gate_decision==INSUFFICIENT_HISTORY) AND (direction==L` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(regime==RANGE) AND (weekday==0.0) AND (atr_pct<=q25(633.4)) AND (gate_decision==INSUFFICIENT_HISTORY)` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(regime==RANGE) AND (gate_decision==INSUFFICIENT_HISTORY) AND (atr_pct<=q25(633.4)) AND (hour in [18,24)) AND (direction` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(regime==RANGE) AND (gate_decision==INSUFFICIENT_HISTORY) AND (atr_pct<=q25(633.4)) AND (hour in [18,24))` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(regime==RANGE) AND (gate_decision==INSUFFICIENT_HISTORY) AND (atr_pct<=q25(633.4)) AND (direction==LONG)` | 100.0 | BIRTH | None | 0.0 | 0.7637 |
| `edge:(gate_decision==INSUFFICIENT_HISTORY) AND (regime==RANGE) AND (direction==LONG) AND (atr_pct<=q25(633.4))` | 100.0 | BIRTH | None | 0.0 | 0.7637 |

## Lifecycle counts

```json
{
  "BIRTH": 75,
  "DECAY": 2
}
```

## Drift statistics

```json
{
  "n_signals": 77,
  "mean_drift": 0.026,
  "high_drift": 2,
  "med_drift": 0,
  "low_drift": 75,
  "mean_half_life_days": null,
  "median_half_life_days": null
}
```

## Promotion

```json
{
  "status": "RETIRE_WEAK",
  "recommendation": "Weak/dead signals dominate \u2014 prefer retirement over promotion.",
  "auto_promotion": false,
  "promote_candidates": [],
  "retire_candidates": [
    "g31_family:LONG",
    "lake:regime:RANGE|LONG"
  ],
  "n_strong": 0,
  "n_weak": 2
}
```

## Integrity

- research_only: True
- gate_unchanged: True
- strategy_unchanged: True
- paper_unchanged: True
- execution_unchanged: True
- optimizer_unchanged: True
