# FEATURE_IMPORTANCE

Consensus ranking: Mutual Information + Information Gain + Permutation Importance + SHAP (if installed).

- shap_available: False

| rank | feature | consensus | MI | IG | perm | SHAP |
|---|---|---|---|---|---|---|
| 1 | `symbol` | 1.0 | 0.549311 | None | None | None |
| 2 | `time` | 0.7599 | None | 0.7599 | None | None |
| 3 | `regime` | 0.7598 | None | 0.7598 | None | None |
| 4 | `direction` | 0.7199 | None | 0.7199 | None | None |
| 5 | `atr_pct` | 0.5823 | 0.134857 | 1.0 | 0.026743 | None |
| 6 | `oi_delta` | 0.5344 | 0.037831 | None | -0.053332 | None |
| 7 | `gate_decision` | 0.3776 | 0.013254 | 0.7311 | None | None |
| 8 | `atr` | 0.1902 | 0.208906 | None | 0.0 | None |
| 9 | `trend` | 0.0722 | 0.079318 | None | 0.0 | None |
| 10 | `funding` | 0.0472 | 0.051814 | None | 0.0 | None |
| 11 | `hour` | 0.0044 | 0.004838 | None | 0.0 | None |
| 12 | `volume` | 0.0 | 0.0 | None | 0.0 | None |
| 13 | `fear_greed` | 0.0 | 0.0 | None | 0.0 | None |
