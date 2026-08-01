# CAUSALITY_REPORT

_Market Causality Engine V1 — from correlation to causation. Research only. Gate / Strategy / Paper / Optimizer / Execution unchanged._

## Run

- trades analyzed: **50**
- causal rows: **50**
- causal relations (edges): **6**
- coverage: **100.0%**
- runtime: **0.045s**
- lake source: `research_lake_v1`
- temporal_leakage_rejected: True

## Dominant causes

- `funding`: 50.8445%
- `atr`: 20.9606%
- `oi`: 9.6287%
- `pattern`: 4.4593%
- `regime`: 4.4593%
- `symbol`: 4.4593%
- `time_of_day`: 3.6002%
- `fear`: 1.5881%
- `rsi`: 0.0%
- `ema`: 0.0%

## Causal graph (top edges)

```
Causal graph (top edges)

funding --(0.08)--> oi
atr --(0.08)--> breakout
pattern --(0.08)--> breakout
regime --(0.08)--> breakout
symbol --(0.08)--> regime
breakout --(0.0288)--> outcome
```

## Stability statistics

- overall stable: **True**
- checks passed: 3/4
- walk-forward: {"ok": true, "stability": 0.9583, "folds": [{"fold": 0, "n": 12, "overlap": 0.8333, "n_edges": 5}, {"fold": 1, "n": 12, "overlap": 1.0, "n_edges": 6}, {"fold": 2, "n": 12, "overlap": 1.0, "n_edges": 6}, {"fold": 3, "n": 14, "overlap": 1.0, "n_edges": 6}]}
- bootstrap: {"ok": true, "stability": 1.0, "n_boot": 12, "ci95": [1.0, 1.0]}
- permutation: {"ok": false, "p_value": 1.0, "true_outcome_weight": 0.0288, "n_perm": 15}
- regimes: 1.0

## Integrity

- gate_unchanged: True
- paper_unchanged: True
- optimizer_unchanged: True
- execution_unchanged: True
- no_n_plus_1_sql: True
