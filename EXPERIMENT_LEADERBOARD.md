# Experiment Leaderboard V1

_Offline side-by-side replay. Read-only. No paper-trading impact._

- Universe trades: **50**
- Baseline: **production_baseline** (Current Production Strategy)
- Experiments run: **6**
- Auto-deploy: **False** (always false)

## Baseline — Current Production Strategy

| Metric | Value |
|---|---|
| trades | 50 |
| pf | 0.4379 |
| expectancy | -25.2943 |
| winrate | 36.0 |
| avg_pnl | -25.2943 |
| max_drawdown | 1758.7285 |
| sharpe | -0.2754 |
| mfe | 1.9386 |
| mae | -2.2502 |
| holding_time | 37183.0 |

Params: `{"confidence_threshold": null, "disabled_symbols": [], "label": "production_baseline"}`

## Summary

- **Champion:** _(none — no experiment cleared promotion gates)_
- **Runner-up:** _(none)_
- **Rejected:** 6

## Leaderboard

| Rank | Role | Experiment | ValScore | PF | Exp | N | Conf | PFΔ | ExpΔ | DDΔ | Promote |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | REJECTED | `conf_gate_055` | -62.0 | 0.4379 | -25.2943 | 50 | 0.575 | 0.0 | 0.0 | 0.0 | no |
| 2 | REJECTED | `conf_gate_065` | -62.0 | 0.4379 | -25.2943 | 50 | 0.575 | 0.0 | 0.0 | 0.0 | no |
| 3 | REJECTED | `conf_gate_075` | -62.0 | 0.4379 | -25.2943 | 50 | 0.575 | 0.0 | 0.0 | 0.0 | no |
| 4 | REJECTED | `disable_sol` | -66.5595 | 0.4432 | -25.5307 | 48 | 0.5759 | 0.0053 | -0.2364 | 10.1509 | no |
| 5 | REJECTED | `bundle_conf065_no_eth` | -107.0655 | 0.2796 | -34.4914 | 47 | 0.5473 | -0.1583 | -9.1971 | 0.0 | no |
| 6 | REJECTED | `disable_eth` | -107.0655 | 0.2796 | -34.4914 | 47 | 0.5473 | -0.1583 | -9.1971 | 0.0 | no |

## Per-experiment detail

### `conf_gate_055` — REJECTED

- Name: Confidence gate 0.55
- Compared to: **baseline only** (`production_baseline`)
- Promotion recommended: `False`
- Rejection: validation_failed, confidence 0.575 < EXP_MIN_CONFIDENCE 0.65, pf_delta 0.0 < EXP_MIN_PF_INCREASE 0.05, p_value 1.0 > EXP_P_VALUE_MAX 0.1, validation:sample_n 15 < VAL_MIN_SAMPLE 30, validation:confidence 0.0 < VAL_MIN_CONFIDENCE 0.65, validation:pf_delta 0.0 < VAL_MIN_PF_INCREASE 0.05, validation:p_value 1.0 > VAL_P_VALUE_MAX 0.1
- Metrics: trades=50 PF=0.4379 exp=-25.2943 WR=36.0 avgPnL=-25.2943 maxDD=1758.7285 sharpe=-0.2754 MFE=1.9386 MAE=-2.2502 hold=37183.0

### `conf_gate_065` — REJECTED

- Name: Confidence gate 0.65
- Compared to: **baseline only** (`production_baseline`)
- Promotion recommended: `False`
- Rejection: validation_failed, confidence 0.575 < EXP_MIN_CONFIDENCE 0.65, pf_delta 0.0 < EXP_MIN_PF_INCREASE 0.05, p_value 1.0 > EXP_P_VALUE_MAX 0.1, validation:sample_n 15 < VAL_MIN_SAMPLE 30, validation:confidence 0.0 < VAL_MIN_CONFIDENCE 0.65, validation:pf_delta 0.0 < VAL_MIN_PF_INCREASE 0.05, validation:p_value 1.0 > VAL_P_VALUE_MAX 0.1
- Metrics: trades=50 PF=0.4379 exp=-25.2943 WR=36.0 avgPnL=-25.2943 maxDD=1758.7285 sharpe=-0.2754 MFE=1.9386 MAE=-2.2502 hold=37183.0

### `conf_gate_075` — REJECTED

- Name: Confidence gate 0.75
- Compared to: **baseline only** (`production_baseline`)
- Promotion recommended: `False`
- Rejection: validation_failed, confidence 0.575 < EXP_MIN_CONFIDENCE 0.65, pf_delta 0.0 < EXP_MIN_PF_INCREASE 0.05, p_value 1.0 > EXP_P_VALUE_MAX 0.1, validation:sample_n 15 < VAL_MIN_SAMPLE 30, validation:confidence 0.0 < VAL_MIN_CONFIDENCE 0.65, validation:pf_delta 0.0 < VAL_MIN_PF_INCREASE 0.05, validation:p_value 1.0 > VAL_P_VALUE_MAX 0.1
- Metrics: trades=50 PF=0.4379 exp=-25.2943 WR=36.0 avgPnL=-25.2943 maxDD=1758.7285 sharpe=-0.2754 MFE=1.9386 MAE=-2.2502 hold=37183.0

### `disable_sol` — REJECTED

- Name: Disable SOL symbol
- Compared to: **baseline only** (`production_baseline`)
- Promotion recommended: `False`
- Rejection: validation_failed, confidence 0.5759 < EXP_MIN_CONFIDENCE 0.65, pf_delta 0.0053 < EXP_MIN_PF_INCREASE 0.05, expectancy_delta -0.2364 < EXP_MIN_EXPECTANCY_INCREASE 0.0, drawdown_delta 10.1509 > EXP_MAX_DRAWDOWN_INCREASE 5.0, p_value 1.0 > EXP_P_VALUE_MAX 0.1, validation:sample_n 15 < VAL_MIN_SAMPLE 30, validation:confidence 0.0 < VAL_MIN_CONFIDENCE 0.65
- Metrics: trades=48 PF=0.4432 exp=-25.5307 WR=35.42 avgPnL=-25.5307 maxDD=1768.8794 sharpe=-0.273 MFE=1.9618 MAE=-2.2925 hold=38016.5

### `bundle_conf065_no_eth` — REJECTED

- Name: Bundle: conf≥0.65 + no ETH
- Compared to: **baseline only** (`production_baseline`)
- Promotion recommended: `False`
- Rejection: validation_failed, confidence 0.5473 < EXP_MIN_CONFIDENCE 0.65, pf_delta -0.1583 < EXP_MIN_PF_INCREASE 0.05, expectancy_delta -9.1971 < EXP_MIN_EXPECTANCY_INCREASE 0.0, p_value 1.0 > EXP_P_VALUE_MAX 0.1, validation:sample_n 15 < VAL_MIN_SAMPLE 30, validation:confidence 0.0 < VAL_MIN_CONFIDENCE 0.65, validation:pf_delta -1.195 < VAL_MIN_PF_INCREASE 0.05
- Metrics: trades=47 PF=0.2796 exp=-34.4914 WR=31.91 avgPnL=-34.4914 maxDD=1758.7285 sharpe=-0.3967 MFE=1.508 MAE=-2.3938 hold=33509.6

### `disable_eth` — REJECTED

- Name: Disable ETH symbol
- Compared to: **baseline only** (`production_baseline`)
- Promotion recommended: `False`
- Rejection: validation_failed, confidence 0.5473 < EXP_MIN_CONFIDENCE 0.65, pf_delta -0.1583 < EXP_MIN_PF_INCREASE 0.05, expectancy_delta -9.1971 < EXP_MIN_EXPECTANCY_INCREASE 0.0, p_value 1.0 > EXP_P_VALUE_MAX 0.1, validation:sample_n 15 < VAL_MIN_SAMPLE 30, validation:confidence 0.0 < VAL_MIN_CONFIDENCE 0.65, validation:pf_delta -1.195 < VAL_MIN_PF_INCREASE 0.05
- Metrics: trades=47 PF=0.2796 exp=-34.4914 WR=31.91 avgPnL=-34.4914 maxDD=1758.7285 sharpe=-0.3967 MFE=1.508 MAE=-2.3938 hold=33509.6

## Safety

- Experiments are **read-only**.
- No writes to production strategy / optimizer applied state.
- No auto deployment.
- Paper trading is unaffected.
