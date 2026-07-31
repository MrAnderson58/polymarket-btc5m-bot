# ALPHA_DISCOVERY_REPORT

_Alpha Discovery Engine V1 — research only. No Gate / Trading / Paper / Optimizer / Execution changes._

- Corpus trades: **50**
- Atomic rules: **69**
- Rules tested: **5000**
- Candidates after filters: **1910**
- Significant (FDR≤0.10): **1161**
- min_n: **8**

> **Note:** With a small local book (n≪1000), many FDR hits are exploratory. Re-run on the full S42 corpus (`ALPHA_ENGINE_LIMIT=100000`) before promoting any alpha.

## Baseline (all trades)

- n=50 WR=36.0 EV=-25.2943 PF=0.4379 Sharpe=-0.2754

## Top alpha candidates

| Rank | Score | Label | n | WR | EV | PF | Sharpe | CI | p | q(FDR) |
|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---:|
| 1 | 49.7831 | `(atr_pct<=median(0.1506)) AND (stoch_k<=q25(41.67))` | 8 | 87.5 | 77.5197 | 13.5557 | 1.3109 | (36.6818, 110.2944) | 0.008264 | 0.02023 |
| 2 | 49.7831 | `(atr_pct<=median(0.1506)) AND (stoch_d<=q25(36.64))` | 8 | 87.5 | 77.5197 | 13.5557 | 1.3109 | (31.7324, 112.7733) | 0.008264 | 0.02023 |
| 3 | 47.2348 | `(rsi in [30.0,45.0)) AND (ema20_distance<=q25(-1.879))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (41.6663, 101.9303) | 0.008264 | 0.02023 |
| 4 | 47.2348 | `(rsi in [30.0,45.0)) AND (ema50_distance<=q25(-1.907))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.4034, 105.2214) | 0.008264 | 0.02023 |
| 5 | 47.2348 | `(rsi in [30.0,45.0)) AND (ema200_distance<=q25(-1.685))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.1119, 101.7531) | 0.008264 | 0.02023 |
| 6 | 47.2348 | `(rsi in [30.0,45.0)) AND (vwap_distance<=q25(-2.068))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.7104, 106.0515) | 0.008264 | 0.02023 |
| 7 | 47.2348 | `(ema20_distance<=q25(-1.879)) AND (stoch_k<=median(50.93))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (45.2146, 103.9119) | 0.008264 | 0.02023 |
| 8 | 47.2348 | `(ema20_distance<=q25(-1.879)) AND (stoch_k<=q25(41.67))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (37.4169, 100.1523) | 0.008264 | 0.02023 |
| 9 | 47.2348 | `(ema20_distance<=q25(-1.879)) AND (stoch_d<=q25(36.64))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (40.7454, 109.8572) | 0.008264 | 0.02023 |
| 10 | 47.2348 | `(ema20_distance<=q25(-1.879)) AND (bb_pct_b<=q25(0.2888))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (34.7907, 100.3792) | 0.008264 | 0.02023 |
| 11 | 47.2348 | `(ema50_distance<=q25(-1.907)) AND (stoch_k<=median(50.93))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.1304, 101.6523) | 0.008264 | 0.02023 |
| 12 | 47.2348 | `(ema50_distance<=q25(-1.907)) AND (stoch_k<=q25(41.67))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (40.6434, 103.1858) | 0.008264 | 0.02023 |
| 13 | 47.2348 | `(ema50_distance<=q25(-1.907)) AND (stoch_d<=q25(36.64))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.8648, 106.1109) | 0.008264 | 0.02023 |
| 14 | 47.2348 | `(ema50_distance<=q25(-1.907)) AND (bb_pct_b<=q25(0.2888))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.1996, 104.2478) | 0.008264 | 0.02023 |
| 15 | 47.2348 | `(ema200_distance<=q25(-1.685)) AND (stoch_k<=median(50.93))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (38.6384, 103.9635) | 0.008264 | 0.02023 |
| 16 | 47.2348 | `(ema200_distance<=q25(-1.685)) AND (stoch_k<=q25(41.67))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (35.3708, 99.5029) | 0.008264 | 0.02023 |
| 17 | 47.2348 | `(ema200_distance<=q25(-1.685)) AND (stoch_d<=q25(36.64))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (45.3723, 107.0949) | 0.008264 | 0.02023 |
| 18 | 47.2348 | `(ema200_distance<=q25(-1.685)) AND (bb_pct_b<=q25(0.2888))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (40.2746, 113.4603) | 0.008264 | 0.02023 |
| 19 | 47.2348 | `(vwap_distance<=q25(-2.068)) AND (stoch_k<=median(50.93))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.991, 101.0081) | 0.008264 | 0.02023 |
| 20 | 47.2348 | `(vwap_distance<=q25(-2.068)) AND (stoch_k<=q25(41.67))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (27.3864, 103.0206) | 0.008264 | 0.02023 |
| 21 | 47.2348 | `(vwap_distance<=q25(-2.068)) AND (stoch_d<=q25(36.64))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (46.7275, 109.4764) | 0.008264 | 0.02023 |
| 22 | 47.2348 | `(vwap_distance<=q25(-2.068)) AND (bb_pct_b<=q25(0.2888))` | 9 | 88.89 | 73.8277 | inf | 1.4161 | (39.2457, 107.0212) | 0.008264 | 0.02023 |
| 23 | 46.304 | `(ema20_distance<=q25(-1.879)) AND (atr_pct<=median(0.1506))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (25.3169, 100.6583) | 0.008264 | 0.02023 |
| 24 | 46.304 | `(ema20_distance<=q25(-1.879)) AND (macd<=median(-0.0004944))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (29.1922, 103.0468) | 0.008264 | 0.02023 |
| 25 | 46.304 | `(ema20_distance<=q25(-1.879)) AND (macd_hist<=q25(-0.001839))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (26.3656, 100.8455) | 0.008264 | 0.02023 |
| 26 | 46.304 | `(ema50_distance<=q25(-1.907)) AND (atr_pct<=median(0.1506))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (26.1315, 100.5157) | 0.008264 | 0.02023 |
| 27 | 46.304 | `(ema50_distance<=q25(-1.907)) AND (macd<=median(-0.0004944))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (28.3009, 104.9582) | 0.008264 | 0.02023 |
| 28 | 46.304 | `(ema50_distance<=q25(-1.907)) AND (macd_hist<=q25(-0.001839))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (29.5303, 99.2587) | 0.008264 | 0.02023 |
| 29 | 46.304 | `(ema200_distance<=q25(-1.685)) AND (atr>median(0.004943))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (26.9081, 102.2322) | 0.008264 | 0.02023 |
| 30 | 46.304 | `(ema200_distance<=q25(-1.685)) AND (macd<=median(-0.0004944))` | 9 | 66.67 | 68.5973 | 15.6915 | 1.1551 | (34.0408, 104.8515) | 0.008264 | 0.02023 |

## Alpha clusters

- **`atr_pct+stoch_k`** size=1 best_EV=77.5197 best_PF=13.5557 score=49.7831 sig_fdr=1
  - (atr_pct<=median(0.1506)) AND (stoch_k<=q25(41.67))
- **`atr_pct+stoch_d`** size=1 best_EV=77.5197 best_PF=13.5557 score=49.7831 sig_fdr=1
  - (atr_pct<=median(0.1506)) AND (stoch_d<=q25(36.64))
- **`ema20_distance+stoch_k`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema20_distance<=q25(-1.879)) AND (stoch_k<=median(50.93))
- **`ema20_distance+stoch_d`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema20_distance<=q25(-1.879)) AND (stoch_d<=q25(36.64))
- **`bb_pct_b+ema20_distance`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema20_distance<=q25(-1.879)) AND (bb_pct_b<=q25(0.2888))
- **`ema50_distance+stoch_k`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema50_distance<=q25(-1.907)) AND (stoch_k<=median(50.93))
- **`ema50_distance+stoch_d`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema50_distance<=q25(-1.907)) AND (stoch_d<=q25(36.64))
- **`bb_pct_b+ema50_distance`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema50_distance<=q25(-1.907)) AND (bb_pct_b<=q25(0.2888))
- **`ema200_distance+stoch_k`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema200_distance<=q25(-1.685)) AND (stoch_k<=median(50.93))
- **`ema200_distance+stoch_d`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema200_distance<=q25(-1.685)) AND (stoch_d<=q25(36.64))
- **`bb_pct_b+ema200_distance`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (ema200_distance<=q25(-1.685)) AND (bb_pct_b<=q25(0.2888))
- **`stoch_k+vwap_distance`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (vwap_distance<=q25(-2.068)) AND (stoch_k<=median(50.93))
- **`stoch_d+vwap_distance`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (vwap_distance<=q25(-2.068)) AND (stoch_d<=q25(36.64))
- **`bb_pct_b+vwap_distance`** size=2 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=2
  - (vwap_distance<=q25(-2.068)) AND (bb_pct_b<=q25(0.2888))
- **`ema20_distance+rsi`** size=1 best_EV=73.8277 best_PF=inf score=47.2348 sig_fdr=1
  - (rsi in [30.0,45.0)) AND (ema20_distance<=q25(-1.879))

## Heatmap summary

- Features in top-100 co-occurrence: atr, atr_pct, bb_pct_b, direction, ema200_distance, ema20_distance, ema50_distance, macd, macd_hist, market_regime, oi_delta, rsi, slope, stoch_d, stoch_k, trend, vwap_distance

## Safety

- Observe-only research module
- No auto-apply to Gate / Trading / Paper / Optimizer / Execution
