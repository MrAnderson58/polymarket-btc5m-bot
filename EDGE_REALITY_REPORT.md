# EDGE_REALITY_REPORT

_Edge Reality Audit V1 — research-only module attribution. Does not modify Paper / Execution / Strategy / Gate / Optimizer / Brain._

## Run

- closed S42 trades: **19160**
- runtime: **43.274s**
- source: `research_lake_v1`
- run_id: `era1-d331e3497d`

## Module attribution (vs production)

| module | ΔEV | ΔPF | ΔWR | MI | IG | Prec | Rec | F1 | grade |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `production` | 0.0 | 0.0 | 0.0 | 0.155276 | 0.155276 | 0.1313 | 0.2875 | 0.1803 | BASE |
| `replay` | 9024.4071 | 1.9097 | 0.4129 | 0.990774 | 0.990774 | 0.54 | 0.0031 | 0.0061 | A+ |
| `edge` | 23041.5482 | 7.4177 | 0.3217 | 0.99396 | 0.99396 | 0.4543 | 0.9969 | 0.6241 | A+ |
| `alpha` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `optimizer` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `brain` | 10009.8632 | None | 0.4131 | 0.99396 | 0.99396 | 1.0 | 0.0031 | 0.0062 | REMOVE |
| `causality` | 27541.887 | None | 0.8674 | 0.0 | 0.0 | 1.0 | 1.0 | 1.0 | A+ |
| `evolution` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `features` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |
| `validation` | 7759.6938 | None | 0.4131 | 0.99396 | 0.99396 | None | 0.0 | None | REMOVE |

## Incremental value

| step | added | ΔEV vs prev | ΔEV vs prod | EV |
|---:|---|---:|---:|---:|
| 0 | `None` | None | None | -7759.6938 |
| 1 | `replay` | 1264.7133 | 1264.7133 | -6494.9805 |
| 2 | `edge` | 10894.7763 | 12159.4896 | 4399.7958 |
| 3 | `alpha` | 0.0 | 12159.4896 | 4399.7958 |
| 4 | `causality` | 15152.5077 | 27311.9973 | 19552.3035 |
| 5 | `brain` | -12672.4486 | 14639.5487 | 6879.8549 |
| 6 | `evolution` | -4387.0781 | 10252.4706 | 2492.7768 |

## Remove-one (ensemble hurt)

| removed | hurt_score (ΔEV loss) | ΔEV | ΔPF | ΔWR |
|---|---:|---:|---:|---:|
| `replay` | -0.0 | 0.0 | None | 0.0 |
| `edge` | -0.0 | 0.0 | None | 0.0 |
| `alpha` | -0.0 | 0.0 | None | 0.0 |
| `optimizer` | -0.0 | 0.0 | None | 0.0 |
| `brain` | -0.0 | 0.0 | None | 0.0 |
| `causality` | -0.0 | 0.0 | None | 0.0 |
| `evolution` | -0.0 | 0.0 | None | 0.0 |
| `features` | -0.0 | 0.0 | None | 0.0 |
| `validation` | -0.0 | 0.0 | None | 0.0 |

## Brain without X

| ablation | hurt_score | ΔEV |
|---|---:|---:|
| Brain without Replay | 2250.1694 | -2250.1694 |
| Brain without Causality | 2250.1694 | -2250.1694 |
| Brain without Edge | -0.0 | 0.0 |
| Brain without Alpha | -0.0 | 0.0 |
| Brain without Optimizer | -0.0 | 0.0 |
| Brain without Validation | -0.0 | 0.0 |
| Brain without Features | -0.0 | 0.0 |
| Brain without Evolution | -0.0 | 0.0 |

**Hurts Brain most when removed:** `replay` (hurt=2250.1694)

## Grades

| module | grade | score | keep |
|---|---|---:|---|
| `causality` | A+ | 75.0 | YES |
| `edge` | A+ | 39.9711 | YES |
| `replay` | A+ | 33.3535 | YES |
| `optimizer` | REMOVE | -5.0302 | NO |
| `features` | REMOVE | -5.0302 | NO |
| `validation` | REMOVE | -5.0302 | NO |
| `alpha` | REMOVE | -10.0302 | NO |
| `evolution` | REMOVE | -16.6113 | NO |
| `brain` | REMOVE | -28.4213 | NO |

## Pareto

- modules capturing ~80% power: **['causality', 'edge']**
- fraction of modules: **0.2222** (2/9)
- power captured: **0.8895**

## Simplification

- **keep:** ['causality', 'edge', 'replay']
- **remove:** ['optimizer', 'features', 'validation', 'alpha', 'evolution', 'brain']
- estimated EV gain after dropping REMOVE: **17059.5267**

## Complexity

| module | runtime_ms | loc | edge/sec | edge/1k LOC |
|---|---:|---:|---:|---:|
| `replay` | 158.809 | 1532 | 56825.5765 | 5890.6052 |
| `edge` | 158.809 | 2812 | 145089.7822 | 8194.0072 |
| `alpha` | 158.809 | 2197 | 48861.8331 | 3531.9498 |
| `optimizer` | 158.809 | 0 | 48861.8331 | None |
| `brain` | 158.809 | 1344 | 63030.8718 | 7447.8149 |
| `causality` | 158.809 | 1210 | 173427.8596 | 22761.8901 |
| `evolution` | 158.809 | 1278 | 48861.8331 | 6071.7479 |
| `features` | 158.809 | 1492 | 48861.8331 | 5200.8672 |
| `validation` | 158.809 | 1295 | 48861.8331 | 5992.0415 |

## Integrity

- research_only: True
- paper_unchanged: True
- execution_unchanged: True
- strategy_unchanged: True
- gate_unchanged: True
- optimizer_unchanged: True
- brain_unchanged: True
