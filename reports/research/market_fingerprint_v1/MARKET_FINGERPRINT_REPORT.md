# MARKET_FINGERPRINT_REPORT

_Market State Fingerprint Engine V1 — research only._

- trades: **19205** snapshots: **18247**
- candles symbols: **20** runtime: **19.039s**
- clusters: **18**

## Current Market

- symbol/dir: `ADA SHORT`
- regime: `RANGE`
- Similarity: **7.17%**
- Closest Fingerprint: **RANGE_SHORT_L01**
- Historical WR/PF/EV: **42.0** / **0.6817** / **-2.9709**
- Recommendation: **RESEARCH ONLY**

## WIN fingerprints

- `UNK_SHORT_W07` n=1202 WR=100.0 PF=inf EV=3.1836 Sharpe=0.5927
- `UNK_SHORT_W06` n=222 WR=100.0 PF=inf EV=2.9137 Sharpe=1.8703
- `UNK_SHORT_W02` n=1041 WR=100.0 PF=inf EV=2.5802 Sharpe=0.8477
- `UNK_LONG_W03` n=782 WR=100.0 PF=inf EV=1.8696 Sharpe=1.1226
- `UNK_SHORT_W01` n=2651 WR=100.0 PF=inf EV=1.843 Sharpe=0.3816
- `UNK_SHORT_W04` n=114 WR=100.0 PF=inf EV=1.8018 Sharpe=0.7901
- `UNK_SHORT_W05` n=69 WR=100.0 PF=inf EV=1.6409 Sharpe=1.6515
- `UNK_SHORT_W00` n=2639 WR=100.0 PF=inf EV=1.5536 Sharpe=0.9271

## LOSS fingerprints

- `RANGE_SHORT_L01` n=32 WR=0.0 PF=0.00 EV=-27.3102
- `RANGE_LONG_L00` n=24 WR=0.0 PF=0.00 EV=-92.0061

## MAE / MFE

- avg MAE=-2.7519 median=-1.5196 | avg MFE=1.9087 median=1.459

## Sequences

### lookback=3
- WIN×3→SHORT n=1386 WR=74.82 PF=34.26 EV=1.8142
- WIN×3→LONG n=1058 WR=32.33 PF=inf EV=0.8443
### lookback=5
- WIN×5→SHORT n=464 WR=80.39 PF=91.97 EV=2.2765
- WIN×5→LONG n=373 WR=39.68 PF=inf EV=1.2812
### lookback=10
- WIN×10→LONG n=73 WR=64.38 PF=inf EV=2.3321
- WIN×10→SHORT n=65 WR=93.85 PF=inf EV=3.5922

## Regime transitions (top)

- BULL → 
- RANGE → RANGE=0.9348, UNK=0.0652
- BEAR → 
- PANIC → 
- RECOVERY → 
- UNK → UNK=0.9996, RANGE=0.0004

## Coin DNA

- **BTC** n=961 PF=32.85 ideal=UNK_SHORT_W00
- **ETH** n=961 PF=54.47 ideal=UNK_SHORT_W00
- **SOL** n=961 PF=8.59 ideal=UNK_SHORT_W00
- **LINK** n=961 PF=16.07 ideal=UNK_SHORT_W01
- **XRP** n=961 PF=6.87 ideal=UNK_SHORT_W01
- **BNB** n=960 PF=15.91 ideal=UNK_SHORT_W01
- **AVAX** n=962 PF=2.98 ideal=UNK_SHORT_W01
- **DOGE** n=960 PF=4.52 ideal=UNK_SHORT_W00

## Universal DNA

- `UNK_SHORT_W01` coins=18 n=2651 PF=inf EV=1.843
- `UNK_SHORT_W00` coins=18 n=2639 PF=inf EV=1.5536
- `UNK_SHORT_W07` coins=18 n=1202 PF=inf EV=3.1836
- `UNK_SHORT_W02` coins=17 n=1041 PF=inf EV=2.5802
- `UNK_SHORT_W04` coins=16 n=114 PF=inf EV=1.8018
- `UNK_LONG_W03` coins=15 n=782 PF=inf EV=1.8696
- `UNK_SHORT_W06` coins=15 n=222 PF=inf EV=2.9137

## Integrity

- research_only: True
- gate/strategy/paper/execution/optimizer/brain unchanged
