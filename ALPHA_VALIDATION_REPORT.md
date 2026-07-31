# ALPHA_VALIDATION_REPORT

_Alpha Validation Engine V2 — research only. No Gate / Strategy / Paper / Execution changes._

- run_id: `av2-1785533910-cc9144a3`
- corpus trades: **50**
- candidates tested: **30**
- PASSED: **20**
- REJECTED: **6**
- INSUFFICIENT: **4**

> Discovery candidates are hypotheses only. Production requires PASSED on walk-forward, rolling windows, OOS replay, and temporal stability (no independent window may lose edge).

## PASSED alphas

| Rule | n | WR | EV | PF | CI | p | WF | Roll | OOS | Stab |
|---|---:|---:|---:|---:|---|---:|:---:|:---:|:---:|:---:|
| `(rsi in [30.0,45.0)) AND (ema20_distance<=q25(-1.879))` | 6 | 83.33 | 60.2391 | None | (10.6383, 117.9187) | 0.049383 | Y | Y | Y | Y |
| `(rsi in [30.0,45.0)) AND (ema50_distance<=q25(-1.907))` | 6 | 83.33 | 60.2391 | None | (20.2261, 99.8311) | 0.024691 | Y | Y | Y | Y |
| `(ema20_distance<=q25(-1.879)) AND (stoch_k<=median(50.93))` | 6 | 83.33 | 60.2391 | None | (19.8052, 111.5416) | 0.037037 | Y | Y | Y | Y |
| `(ema20_distance<=q25(-1.879)) AND (stoch_k<=q25(41.67))` | 6 | 83.33 | 60.2391 | None | (1.8835, 101.4542) | 0.024691 | Y | Y | Y | Y |
| `(ema20_distance<=q25(-1.879)) AND (stoch_d<=q25(36.64))` | 6 | 83.33 | 60.2391 | None | (20.0262, 100.7782) | 0.012346 | Y | Y | Y | Y |
| `(ema20_distance<=q25(-1.879)) AND (bb_pct_b<=q25(0.2888))` | 6 | 83.33 | 60.2391 | None | (19.6053, 100.5783) | 0.037037 | Y | Y | Y | Y |
| `(ema50_distance<=q25(-1.907)) AND (stoch_k<=median(50.93))` | 6 | 83.33 | 60.2391 | None | (20.4471, 117.0428) | 0.012346 | Y | Y | Y | Y |
| `(ema50_distance<=q25(-1.907)) AND (stoch_k<=q25(41.67))` | 6 | 83.33 | 60.2391 | None | (19.8052, 101.8751) | 0.024691 | Y | Y | Y | Y |
| `(ema50_distance<=q25(-1.907)) AND (stoch_d<=q25(36.64))` | 6 | 83.33 | 60.2391 | None | (10.8172, 100.9992) | 0.049383 | Y | Y | Y | Y |
| `(ema50_distance<=q25(-1.907)) AND (bb_pct_b<=q25(0.2888))` | 6 | 83.33 | 60.2391 | None | (20.0262, 98.187) | 0.024691 | Y | Y | Y | Y |
| `(ema200_distance<=q25(-1.685)) AND (stoch_d<=q25(36.64))` | 6 | 83.33 | 60.2391 | None | (10.5962, 116.9506) | 0.012346 | Y | Y | Y | Y |
| `(vwap_distance<=q25(-2.068)) AND (stoch_d<=q25(36.64))` | 6 | 83.33 | 60.2391 | None | (20.2261, 101.9673) | 0.037037 | Y | Y | Y | Y |
| `(rsi in [30.0,45.0)) AND (ema200_distance<=q25(-1.685))` | 8 | 87.5 | 58.6132 | None | (25.5471, 93.1416) | 0.024691 | Y | Y | Y | Y |
| `(rsi in [30.0,45.0)) AND (vwap_distance<=q25(-2.068))` | 8 | 87.5 | 58.6132 | None | (31.9326, 92.4155) | 0.012346 | Y | Y | Y | Y |
| `(ema200_distance<=q25(-1.685)) AND (stoch_k<=median(50.93))` | 8 | 87.5 | 58.6132 | None | (29.9865, 90.8825) | 0.012346 | Y | Y | Y | Y |
| `(ema200_distance<=q25(-1.685)) AND (stoch_k<=q25(41.67))` | 8 | 87.5 | 58.6132 | None | (25.3498, 88.7925) | 0.012346 | Y | Y | Y | Y |
| `(ema200_distance<=q25(-1.685)) AND (bb_pct_b<=q25(0.2888))` | 8 | 87.5 | 58.6132 | None | (28.6094, 95.5253) | 0.012346 | Y | Y | Y | Y |
| `(vwap_distance<=q25(-2.068)) AND (stoch_k<=median(50.93))` | 8 | 87.5 | 58.6132 | None | (21.3854, 89.7866) | 0.012346 | Y | Y | Y | Y |
| `(vwap_distance<=q25(-2.068)) AND (stoch_k<=q25(41.67))` | 8 | 87.5 | 58.6132 | None | (21.5634, 96.1285) | 0.012346 | Y | Y | Y | Y |
| `(vwap_distance<=q25(-2.068)) AND (bb_pct_b<=q25(0.2888))` | 8 | 87.5 | 58.6132 | None | (20.9163, 93.3601) | 0.012346 | Y | Y | Y | Y |

## Top rejections

- `(ema200_distance<=q25(-1.685)) AND (atr>median(0.004943))` — **rolling:lost_edge:w2; stability:lost_edge:b2** (n=9, EV=68.5973, PF=15.6915)
- `(ema20_distance<=q25(-1.879)) AND (atr_pct<=median(0.1506))` — **rolling:lost_edge:w2; stability:lost_edge:b2** (n=6, EV=52.3935, PF=8.4807)
- `(ema50_distance<=q25(-1.907)) AND (atr_pct<=median(0.1506))` — **rolling:lost_edge:w2; stability:lost_edge:b2** (n=6, EV=52.3935, PF=8.4807)
- `(ema20_distance<=q25(-1.879)) AND (macd<=median(-0.0004944))` — **rolling:lost_edge:w2; stability:lost_edge:b2** (n=12, EV=51.8689, PF=15.8116)
- `(ema50_distance<=q25(-1.907)) AND (macd<=median(-0.0004944))` — **rolling:lost_edge:w2; stability:lost_edge:b2** (n=12, EV=51.8689, PF=15.8116)
- `(ema200_distance<=q25(-1.685)) AND (macd<=median(-0.0004944))` — **rolling:lost_edge:w2; stability:lost_edge:b2** (n=12, EV=51.8689, PF=15.8116)

## Validation protocol

1. Walk-forward (train / validation / test chronological splits)
2. Rolling non-overlapping windows
3. Out-of-sample replay (70/30 freeze)
4. Monte Carlo subset null + permutation p-value
5. Bootstrap expectancy CI (prefer OOS)
6. Temporal stability of PF / EV / WR
7. Auto-reject if edge lost in any independent window

## Safety

- Observe-only research module
- Tables: `alpha_validations`, `alpha_validation_history`
- No auto-apply to Gate / Strategy / Paper / Execution

## Run summary JSON

```json
{
  "run_id": "av2-1785533910-cc9144a3",
  "n_rows": 50,
  "n_candidates": 30,
  "n_passed": 20,
  "n_rejected": 6,
  "n_insufficient": 4
}
```
