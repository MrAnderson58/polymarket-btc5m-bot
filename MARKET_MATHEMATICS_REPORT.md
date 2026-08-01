# MARKET_MATHEMATICS_REPORT

_Market Mathematics Research V1 — explainable statistics only. No Gate / Strategy / Optimizer / Paper Trading changes._

- Trades analyzed: **50** (full available CLOSED S42×S55 history)
> **Note:** Local analytics DB currently exposes fewer CLOSED rows than a full ~30k production corpus. Re-run `market-math-research` on the host with the complete book.
- Baseline EV=-25.2943 PF=0.4379 WR=36.0 Sharpe=-0.2754
- Elapsed: 0.115s

## TOP rules

| # | Rule | n | WR | EV | PF | CI95 | conf |
|---:|---|---:|---:|---:|---:|---|---:|
| 1 | `atr_pct<=8.7767` | 9 | 66.67 | 47.4383 | 11.1598 | None | 0.3671 |

## Gate filter contribution

- **atr**: USELESS (ΔEV=None, ΔPF=None, rule=`None`)
- **atr_pct**: USELESS (ΔEV=None, ΔPF=None, rule=`None`)
- **funding**: USELESS (ΔEV=None, ΔPF=None, rule=`None`)
- **fear_greed**: USELESS (ΔEV=0.0, ΔPF=0.0, rule=`fear_greed<=22`)
- **trend**: USELESS (ΔEV=0.0, ΔPF=0.0, rule=`trend<=0`)
- **adx**: USELESS (ΔEV=None, ΔPF=None, rule=`None`)
- **rsi**: USELESS (ΔEV=None, ΔPF=None, rule=`None`)
- **oi_delta**: USEFUL (ΔEV=2.3255, ΔPF=0.0688, rule=`oi_delta<=-26873`)

## Symbol grades

- `ETH` [GOOD] n=3 EV=118.7946 PF=inf WR=100.0
- `LINK` [GOOD] n=3 EV=101.005 PF=inf WR=100.0
- `BTC` [GOOD] n=3 EV=37.528 PF=inf WR=100.0
- `APT` [GOOD] n=3 EV=2.7815 PF=1.3333 WR=66.67
- `ADA` [GOOD] n=3 EV=1.6835 PF=inf WR=66.67
- `BNB` [BAD] n=3 EV=-14.0076 PF=0.0 WR=0.0
- `DOGE` [BAD] n=3 EV=-28.0167 PF=0.0 WR=0.0
- `AVAX` [BAD] n=3 EV=-29.3074 PF=0.0 WR=0.0
- `INJ` [BAD] n=3 EV=-81.812 PF=0.0 WR=0.0
- `ARB` [BAD] n=3 EV=-215.2443 PF=0.0 WR=0.0
- `MANTA` [REMOVE] n=2 EV=53.7355 PF=inf WR=100.0
- `PEPE` [REMOVE] n=2 EV=28.7115 PF=inf WR=100.0
- `SOL` [REMOVE] n=2 EV=-19.6207 PF=0.2055 WR=50.0
- `XRP` [REMOVE] n=2 EV=-20.9895 PF=0.0 WR=0.0
- `WIF` [REMOVE] n=2 EV=-22.9455 PF=0.0 WR=0.0
- `SUI` [REMOVE] n=2 EV=-77.1863 PF=0.0 WR=0.0
- `OP` [REMOVE] n=2 EV=-179.8202 PF=0.0 WR=0.0
- `NEAR` [REMOVE] n=2 EV=-234.3485 PF=0.0 WR=0.0
- `MATIC` [REMOVE] n=2 EV=0.0 PF=0.0 WR=0.0
- `TON` [REMOVE] n=2 EV=0.0 PF=0.0 WR=0.0

## Harmful regimes (BLOCK candidates)

- `oi_delta in [-54351.8--54214.4]` n=8 EV=-41.9186 PF=0.0
- `oi<=72 AND oi_delta<=-54210.8` n=20 EV=-29.3469 PF=0.3716
- `oi<=72 AND oi_delta<=-54210.8` n=20 EV=-29.3469 PF=0.3716
- `oi>=72 AND oi_delta<=-54210.8` n=20 EV=-29.3469 PF=0.3716
- `oi_delta<=-54210.8 AND fear_greed<=22` n=20 EV=-29.3469 PF=0.3716
- `oi_delta<=-54210.8 AND fear_greed<=22` n=20 EV=-29.3469 PF=0.3716
- `oi_delta<=-54210.8 AND fear_greed>=22` n=20 EV=-29.3469 PF=0.3716
- `oi_delta<=-54210.8 AND trend<=0` n=20 EV=-29.3469 PF=0.3716
- `oi_delta<=-54210.8 AND trend<=0` n=20 EV=-29.3469 PF=0.3716
- `oi_delta<=-54210.8 AND trend>=0` n=20 EV=-29.3469 PF=0.3716
- `oi<=72 AND oi_delta>-26873` n=25 EV=-27.6198 PF=0.3643
- `oi<=72 AND oi_delta>=72` n=25 EV=-27.6198 PF=0.3643
- `oi<=72 AND oi_delta>-26873` n=25 EV=-27.6198 PF=0.3643
- `oi<=72 AND oi_delta>=72` n=25 EV=-27.6198 PF=0.3643
- `oi>=72 AND oi_delta>-26873` n=25 EV=-27.6198 PF=0.3643

## Market map

- Stable/useful features: oi_delta
- Useless/harmful features: atr, atr_pct, funding, fear_greed, trend, adx, rsi
- Significant triples kept: 0

## Recommendations (observe-only)

- Can remove: 7
- Can strengthen: 31
- Can weaken: 0
- Need check: 1

### Can remove
- `atr`: no meaningful EV/PF lift vs baseline
- `atr_pct`: no meaningful EV/PF lift vs baseline
- `funding`: no meaningful EV/PF lift vs baseline
- `fear_greed`: no meaningful EV/PF lift vs baseline
- `trend`: no meaningful EV/PF lift vs baseline
- `adx`: no meaningful EV/PF lift vs baseline
- `rsi`: no meaningful EV/PF lift vs baseline

### Can strengthen
- `oi_delta`: good_rule=oi_delta<=-26873 delta_ev=2.3255
- `block_regime`: BLOCK oi_delta in [-54351.8--54214.4] (EV=-41.9186, PF=0.0)
- `block_regime`: BLOCK oi<=72 AND oi_delta<=-54210.8 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi<=72 AND oi_delta<=-54210.8 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi>=72 AND oi_delta<=-54210.8 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi_delta<=-54210.8 AND fear_greed<=22 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi_delta<=-54210.8 AND fear_greed<=22 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi_delta<=-54210.8 AND fear_greed>=22 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi_delta<=-54210.8 AND trend<=0 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi_delta<=-54210.8 AND trend<=0 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi_delta<=-54210.8 AND trend>=0 (EV=-29.3469, PF=0.3716)
- `block_regime`: BLOCK oi<=72 AND oi_delta>-26873 (EV=-27.6198, PF=0.3643)

### Can weaken
_none_

## Safety

- Research-only module
- Artifacts under `reports/research/market_math/`
- Gate / Strategy / Optimizer / Paper Trading unchanged
