# ALPHA_DISCOVERY_REPORT

_Alpha Discovery Engine V1 — research only. No Gate / Trading / Paper / Optimizer / Execution changes._

- Corpus trades: **50**
- Atomic rules: **13**
- Rules tested: **104**
- Candidates after filters: **29**
- Significant (FDR≤0.10): **0**
- min_n: **8**

> **Note:** With a small local book (n≪1000), many FDR hits are exploratory. Re-run on the full S42 corpus (`ALPHA_ENGINE_LIMIT=100000`) before promoting any alpha.

## Baseline (all trades)

- n=50 WR=36.0 EV=-25.2943 PF=0.4379 Sharpe=-0.2754

## Top alpha candidates

| Rank | Score | Label | n | WR | EV | PF | Sharpe | CI | p | q(FDR) |
|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---:|
| 1 | 1.1415 | `oi_delta<=median(-2.687e+04)` | 25 | 32.0 | -22.9688 | 0.5067 | -0.2439 | (-51.2673, 17.5327) | 0.803279 | 0.918033 |
| 2 | 1.1415 | `(oi_delta<=median(-2.687e+04)) AND (direction==LONG)` | 25 | 32.0 | -22.9688 | 0.5067 | -0.2439 | (-49.8629, 6.6869) | 0.868852 | 0.918033 |
| 3 | 1.1415 | `(oi_delta<=median(-2.687e+04)) AND (market_regime==RANGE)` | 25 | 32.0 | -22.9688 | 0.5067 | -0.2439 | (-56.0842, 15.7004) | 0.901639 | 0.918033 |
| 4 | 1.1415 | `(oi_delta<=median(-2.687e+04)) AND (direction==LONG) AND (market_regime==RANGE)` | 25 | 32.0 | -22.9688 | 0.5067 | -0.2439 | (-59.2042, 7.5513) | 0.885246 | 0.918033 |
| 5 | -0.987 | `oi_delta>median(-2.687e+04)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-59.6693, -0.8126) | 0.819672 | 0.918033 |
| 6 | -0.987 | `oi_delta>=q75(72)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-55.4729, 3.9821) | 0.868852 | 0.918033 |
| 7 | -0.987 | `(oi_delta>median(-2.687e+04)) AND (direction==LONG)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-61.3906, -2.1924) | 0.803279 | 0.918033 |
| 8 | -0.987 | `(oi_delta>=q75(72)) AND (direction==LONG)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-69.3063, 6.4959) | 0.836066 | 0.918033 |
| 9 | -0.987 | `(oi_delta>median(-2.687e+04)) AND (gate_decision==INSUFFICIENT_HISTORY)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-61.7704, -0.3279) | 0.819672 | 0.918033 |
| 10 | -0.987 | `(oi_delta>=q75(72)) AND (gate_decision==INSUFFICIENT_HISTORY)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-63.279, 0.2007) | 0.836066 | 0.918033 |
| 11 | -0.987 | `(oi_delta>median(-2.687e+04)) AND (market_regime==RANGE)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-64.652, 0.8995) | 0.901639 | 0.918033 |
| 12 | -0.987 | `(oi_delta>=q75(72)) AND (market_regime==RANGE)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-57.7026, -0.2271) | 0.852459 | 0.918033 |
| 13 | -0.987 | `(oi_delta>median(-2.687e+04)) AND (direction==LONG) AND (gate_decision==INSUFFIC` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-65.564, 8.2872) | 0.918033 | 0.918033 |
| 14 | -0.987 | `(oi_delta>median(-2.687e+04)) AND (direction==LONG) AND (market_regime==RANGE)` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-65.5668, -3.1622) | 0.868852 | 0.918033 |
| 15 | -0.987 | `(oi_delta>median(-2.687e+04)) AND (gate_decision==INSUFFICIENT_HISTORY) AND (mar` | 25 | 40.0 | -27.6198 | 0.3643 | -0.309 | (-63.0634, 7.4203) | 0.786885 | 0.918033 |
| 16 | -1.3209 | `gate_decision==INSUFFICIENT_HISTORY` | 45 | 33.33 | -28.3874 | 0.3677 | -0.316 | (-56.3688, 2.5945) | 0.655738 | 0.918033 |
| 17 | -1.3209 | `(direction==LONG) AND (gate_decision==INSUFFICIENT_HISTORY)` | 45 | 33.33 | -28.3874 | 0.3677 | -0.316 | (-53.1184, -4.467) | 0.42623 | 0.918033 |
| 18 | -1.3209 | `(gate_decision==INSUFFICIENT_HISTORY) AND (market_regime==RANGE)` | 45 | 33.33 | -28.3874 | 0.3677 | -0.316 | (-51.0492, -1.0011) | 0.52459 | 0.918033 |
| 19 | -1.3209 | `(direction==LONG) AND (gate_decision==INSUFFICIENT_HISTORY) AND (market_regime==` | 45 | 33.33 | -28.3874 | 0.3677 | -0.316 | (-51.9793, -5.8858) | 0.540984 | 0.918033 |
| 20 | -1.7664 | `oi_delta<=q25(-5.421e+04)` | 20 | 25.0 | -29.3469 | 0.3716 | -0.3248 | (-63.4939, -0.2066) | 0.819672 | 0.918033 |
| 21 | -1.7664 | `(oi_delta<=q25(-5.421e+04)) AND (direction==LONG)` | 20 | 25.0 | -29.3469 | 0.3716 | -0.3248 | (-68.5579, 12.1589) | 0.836066 | 0.918033 |
| 22 | -1.7664 | `(oi_delta<=median(-2.687e+04)) AND (gate_decision==INSUFFICIENT_HISTORY)` | 20 | 25.0 | -29.3469 | 0.3716 | -0.3248 | (-58.1157, -3.4732) | 0.852459 | 0.918033 |
| 23 | -1.7664 | `(oi_delta<=q25(-5.421e+04)) AND (gate_decision==INSUFFICIENT_HISTORY)` | 20 | 25.0 | -29.3469 | 0.3716 | -0.3248 | (-67.5448, 8.0011) | 0.852459 | 0.918033 |
| 24 | -1.7664 | `(oi_delta<=q25(-5.421e+04)) AND (market_regime==RANGE)` | 20 | 25.0 | -29.3469 | 0.3716 | -0.3248 | (-63.3676, 8.0571) | 0.754098 | 0.918033 |
| 25 | -1.7664 | `(oi_delta<=median(-2.687e+04)) AND (direction==LONG) AND (gate_decision==INSUFFI` | 20 | 25.0 | -29.3469 | 0.3716 | -0.3248 | (-64.4285, -5.4589) | 0.868852 | 0.918033 |
| 26 | -1.7664 | `(oi_delta<=median(-2.687e+04)) AND (gate_decision==INSUFFICIENT_HISTORY) AND (ma` | 20 | 25.0 | -29.3469 | 0.3716 | -0.3248 | (-59.7744, -3.3438) | 0.754098 | 0.918033 |
| 27 | -11.3083 | `direction==LONG` | 50 | 36.0 | -25.2943 | 0.4379 | -0.2754 | (-52.1016, 1.5306) | None | None |
| 28 | -11.3083 | `market_regime==RANGE` | 50 | 36.0 | -25.2943 | 0.4379 | -0.2754 | (-51.6767, -4.2048) | None | None |
| 29 | -11.3083 | `(direction==LONG) AND (market_regime==RANGE)` | 50 | 36.0 | -25.2943 | 0.4379 | -0.2754 | (-46.5845, -3.1207) | None | None |

## Alpha clusters

- **`oi_delta`** size=4 best_EV=-22.9688 best_PF=0.5067 score=1.1415 sig_fdr=0
  - oi_delta<=median(-2.687e+04)
- **`direction+oi_delta`** size=4 best_EV=-22.9688 best_PF=0.5067 score=1.1415 sig_fdr=0
  - (oi_delta<=median(-2.687e+04)) AND (direction==LONG)
- **`market_regime+oi_delta`** size=4 best_EV=-22.9688 best_PF=0.5067 score=1.1415 sig_fdr=0
  - (oi_delta<=median(-2.687e+04)) AND (market_regime==RANGE)
- **`direction+market_regime+oi_delta`** size=2 best_EV=-22.9688 best_PF=0.5067 score=1.1415 sig_fdr=0
  - (oi_delta<=median(-2.687e+04)) AND (direction==LONG) AND (market_regime==RANGE)
- **`gate_decision+oi_delta`** size=4 best_EV=-27.6198 best_PF=0.3643 score=-0.987 sig_fdr=0
  - (oi_delta>median(-2.687e+04)) AND (gate_decision==INSUFFICIENT_HISTORY)
- **`direction+gate_decision+oi_delta`** size=2 best_EV=-27.6198 best_PF=0.3643 score=-0.987 sig_fdr=0
  - (oi_delta>median(-2.687e+04)) AND (direction==LONG) AND (gate_decision==INSUFFICIENT_HISTORY)
- **`gate_decision+market_regime+oi_delta`** size=2 best_EV=-27.6198 best_PF=0.3643 score=-0.987 sig_fdr=0
  - (oi_delta>median(-2.687e+04)) AND (gate_decision==INSUFFICIENT_HISTORY) AND (market_regime==RANGE)
- **`gate_decision`** size=1 best_EV=-28.3874 best_PF=0.3677 score=-1.3209 sig_fdr=0
  - gate_decision==INSUFFICIENT_HISTORY
- **`direction+gate_decision`** size=1 best_EV=-28.3874 best_PF=0.3677 score=-1.3209 sig_fdr=0
  - (direction==LONG) AND (gate_decision==INSUFFICIENT_HISTORY)
- **`gate_decision+market_regime`** size=1 best_EV=-28.3874 best_PF=0.3677 score=-1.3209 sig_fdr=0
  - (gate_decision==INSUFFICIENT_HISTORY) AND (market_regime==RANGE)
- **`direction+gate_decision+market_regime`** size=1 best_EV=-28.3874 best_PF=0.3677 score=-1.3209 sig_fdr=0
  - (direction==LONG) AND (gate_decision==INSUFFICIENT_HISTORY) AND (market_regime==RANGE)
- **`direction`** size=1 best_EV=-25.2943 best_PF=0.4379 score=-11.3083 sig_fdr=0
  - direction==LONG
- **`market_regime`** size=1 best_EV=-25.2943 best_PF=0.4379 score=-11.3083 sig_fdr=0
  - market_regime==RANGE
- **`direction+market_regime`** size=1 best_EV=-25.2943 best_PF=0.4379 score=-11.3083 sig_fdr=0
  - (direction==LONG) AND (market_regime==RANGE)

## Heatmap summary

- Features in top-100 co-occurrence: direction, gate_decision, market_regime, oi_delta

## Safety

- Observe-only research module
- No auto-apply to Gate / Trading / Paper / Optimizer / Execution
