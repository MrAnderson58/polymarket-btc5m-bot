# TRADING_RULES_REPORT

_Trading Rules Extraction V1 — research only. No new features; DNA setups minimized for paper._

- corpus: **19160**
- runtime: **12.092s**
- ready: **9**
- hard_block: **1**

## READY FOR PAPER

### 1. ADX>22.5 + MACD>0

- n=2860 WR=52.69 PF=64.6913 EV=0.9358 CI=[0.8786, 1.0125] conf=0.5269 universal=True

### 2. ADX>22.5 + EMA20>EMA50

- n=2880 WR=51.67 PF=64.103 EV=0.9208 CI=[0.85, 0.994] conf=0.5167 universal=True

### 3. ATR<0.6 + ADX>22.5 + MACD>0

- n=2790 WR=52.8 PF=64.0941 EV=0.9503 CI=[0.8924, 1.0234] conf=0.528 universal=True

### 4. EMA20>EMA50 + Confidence>5.68

- n=2706 WR=43.79 PF=56.6294 EV=0.8639 CI=[0.7908, 0.9468] conf=0.5621 universal=True

### 5. EMA20>EMA50 + ADX>22.5 + MACD>0

- n=2581 WR=51.88 PF=56.1948 EV=0.8987 CI=[0.8288, 0.9651] conf=0.5188 universal=True

### 6. EMA20>EMA50 + ADX>22.5 + MACD>0 + ATR<0.6

- n=2514 WR=51.91 PF=55.5975 EV=0.9126 CI=[0.8444, 0.9801] conf=0.5191 universal=True

### 7. EMA20<EMA50 + ATR<0.15

- n=4062 WR=41.43 PF=53.8995 EV=0.5467 CI=[0.5199, 0.5744] conf=0.5857 universal=True

### 8. ATR<0.6 + EMA20>EMA50

- n=6169 WR=50.56 PF=43.4124 EV=0.8668 CI=[0.8172, 0.9184] conf=0.5056 universal=True

### 9. MACD>0 + EMA20>EMA50

- n=5260 WR=50.21 PF=35.8536 EV=0.8354 CI=[0.7814, 0.886] conf=0.5021 universal=True

## HARD BLOCK

### 1. Gate=INSUFFICIENT_HISTORY

- n=45 WR=33.33 PF=0.3677 EV=-28.3874 Action=BLOCK

## Integrity

- research_only: True
- paper_unchanged: True
- execution_unchanged: True
- strategy_unchanged: True
- gate_unchanged: True
- optimizer_unchanged: True
- brain_unchanged: True
