# TRADING_DNA_REPORT

_Trading DNA Discovery V1 — research only. Actual setups, not modules._

- closed trades: **19160**
- runtime: **3.181s**
- candle hit rate: **0.95**

## 1. Segment DNA

| segment | n | PF | EV | WR |
|---|---:|---:|---:|---:|
| TOP_5 | 958 | inf | 6.3027 | 100.0 |
| TOP_10 | 1916 | inf | 4.7995 | 100.0 |
| TOP_20 | 3832 | inf | 3.4967 | 100.0 |
| MIDDLE | 11496 | inf | 0.3595 | 42.38 |
| BOTTOM_20 | 3832 | 0.00 | -0.5872 | 0.0 |
| BOTTOM_10 | 1916 | 0.00 | -1.1744 | 0.0 |
| BOTTOM_5 | 958 | 0.00 | -2.3488 | 0.0 |

## 2. TOP10 vs BOTTOM10 factors

- `oi_delta`: top10=-26872.95 bottom10=-53817.9 Δ=26944.95
- `adx`: top10=22.2264 bottom10=23.4083 Δ=-1.1819
- `rsi`: top10=47.0828 bottom10=48.2646 Δ=-1.1818
- `ema20`: top10=0.7038 bottom10=1.5901 Δ=-0.8863
- `vwap`: top10=0.7222 bottom10=1.6079 Δ=-0.8857
- `ema50`: top10=0.7049 bottom10=1.5901 Δ=-0.8852
- `ema200`: top10=0.7142 bottom10=1.5901 Δ=-0.8759
- `confidence`: top10=5.67 bottom10=6.05 Δ=-0.38
- `direction`: top=SHORT (0.7474) bottom=LONG (0.7249)
- `hour`: top=18 (0.1942) bottom=16 (0.2114)
- `weekday`: top=1 (0.2745) bottom=4 (0.7547)
- `regime`: top=UNK (0.9916) bottom=UNK (0.9859)
- `pattern`: top=g31 (1.0) bottom=g31 (1.0)
- `funding_sign`: top=UNK (0.9916) bottom=UNK (0.9859)

## 3. TOP profitable setups (100)

| # | setup | PF | EV | n | conf |
|---:|---|---:|---:|---:|---:|
| 1 | ADX>22.5 + MACD+ | 64.69 | 0.9358 | 2860 | 0.5269 |
| 2 | Pattern=g31 + ADX>22.5 + MACD+ | 64.69 | 0.9358 | 2860 | 0.5269 |
| 3 | ADX>22.5 + EMA20>EMA50 | 64.10 | 0.9208 | 2880 | 0.5167 |
| 4 | Pattern=g31 + ADX>22.5 + EMA20>EMA50 | 64.10 | 0.9208 | 2880 | 0.5167 |
| 5 | ATR%<0.6 + ADX>22.5 + MACD+ | 64.09 | 0.9503 | 2790 | 0.528 |
| 6 | ATR%<0.6 + ADX>22.5 + EMA20>EMA50 | 63.27 | 0.9379 | 2790 | 0.5154 |
| 7 | EMA20>EMA50 + Confidence>5.68 | 56.63 | 0.8639 | 2706 | 0.5621 |
| 8 | Pattern=g31 + EMA20>EMA50 + Confidence>5.68 | 56.63 | 0.8639 | 2706 | 0.5621 |
| 9 | ATR%<0.6 + EMA20>EMA50 + Confidence>5.68 | 56.38 | 0.868 | 2681 | 0.5625 |
| 10 | ADX>22.5 + MACD+ + EMA20>EMA50 | 56.19 | 0.8987 | 2581 | 0.5188 |
| 11 | EMA20<EMA50 + ATR%<0.15 | 53.90 | 0.5467 | 4062 | 0.5857 |
| 12 | Pattern=g31 + EMA20<EMA50 + ATR%<0.15 | 53.90 | 0.5467 | 4062 | 0.5857 |
| 13 | SHORT + Weekday=Tue | inf | 3.0459 | 1116 | 0.9642 |
| 14 | SHORT + Pattern=g31 + Weekday=Tue | inf | 3.0459 | 1116 | 0.9642 |
| 15 | SHORT + ATR%<0.6 + Weekday=Tue | inf | 3.0447 | 1103 | 0.9637 |
| 16 | SHORT + Weekday=Tue + MACD+ | inf | 2.683 | 699 | 0.9613 |
| 17 | SHORT + Weekday=Tue + EMA20>EMA50 | inf | 2.5445 | 734 | 0.9454 |
| 18 | SHORT + Hour=20 | inf | 2.2511 | 975 | 0.8913 |
| 19 | SHORT + Pattern=g31 + Hour=20 | inf | 2.2511 | 975 | 0.8913 |
| 20 | SHORT + ATR%<0.6 + Hour=20 | inf | 2.2444 | 972 | 0.8909 |
| 21 | SHORT + EMA20<EMA50 + ATR%>0.234 | inf | 2.2427 | 1819 | 0.7334 |
| 22 | SHORT + ATR%>0.234 + Confidence>5.68 | inf | 2.1475 | 1217 | 0.6499 |
| 23 | SHORT + MACD- + ATR%>0.234 | inf | 2.1097 | 1759 | 0.7277 |
| 24 | SHORT + ATR%>0.234 + RSI 30-45 | inf | 2.0631 | 1051 | 0.7297 |
| 25 | ADX<=22.5 + Weekday=Tue | inf | 2.0599 | 1039 | 0.6226 |
| 26 | Pattern=g31 + ADX<=22.5 + Weekday=Tue | inf | 2.0599 | 1039 | 0.6226 |
| 27 | ATR%<0.6 + ADX<=22.5 + Weekday=Tue | inf | 2.0494 | 1036 | 0.6215 |
| 28 | Weekday=Tue + MACD+ | inf | 1.936 | 976 | 0.7253 |
| 29 | Pattern=g31 + Weekday=Tue + MACD+ | inf | 1.936 | 976 | 0.7253 |
| 30 | Weekday=Tue + EMA20>EMA50 | inf | 1.9335 | 967 | 0.7248 |
| 31 | ATR%<0.6 + Weekday=Tue + EMA20>EMA50 | inf | 1.9335 | 967 | 0.7248 |
| 32 | Pattern=g31 + Weekday=Tue + EMA20>EMA50 | inf | 1.9335 | 967 | 0.7248 |
| 33 | ATR%<0.6 + Weekday=Tue + MACD+ | inf | 1.9327 | 975 | 0.7251 |
| 34 | SHORT + EMA20<EMA50 + Confidence>5.68 | inf | 1.9303 | 1896 | 0.7358 |
| 35 | SHORT + MACD- + Confidence>5.68 | inf | 1.8382 | 1834 | 0.6957 |
| 36 | SHORT + EMA20<EMA50 + Weekday=Fri | inf | 1.7544 | 1192 | 0.8683 |
| 37 | SHORT + ADX>22.5 + ATR%>0.234 | inf | 1.7257 | 2083 | 0.6615 |
| 38 | SHORT + EMA20<EMA50 + ADX>22.5 | inf | 1.7135 | 2863 | 0.788 |
| 39 | SHORT + MACD- + Weekday=Fri | inf | 1.7044 | 1075 | 0.8726 |
| 40 | ATR%<0.6 + Weekday=Tue | inf | 1.7017 | 2001 | 0.5832 |
| 41 | ATR%<0.6 + Pattern=g31 + Weekday=Tue | inf | 1.7017 | 2001 | 0.5832 |
| 42 | SHORT + ADX>22.5 + Weekday=Fri | inf | 1.6797 | 907 | 0.8599 |
| 43 | SHORT + ATR%>0.234 + RSI 45-55 | inf | 1.6622 | 1162 | 0.5704 |
| 44 | SHORT + MACD- + ADX>22.5 | inf | 1.6594 | 2878 | 0.7686 |
| 45 | SHORT + ATR%>0.234 | inf | 1.6466 | 3136 | 0.6055 |
| 46 | SHORT + Pattern=g31 + ATR%>0.234 | inf | 1.6466 | 3136 | 0.6055 |
| 47 | Weekday=Tue | inf | 1.6255 | 2120 | 0.5566 |
| 48 | Pattern=g31 + Weekday=Tue | inf | 1.6255 | 2120 | 0.5566 |
| 49 | SHORT + MACD- + RSI 45-55 | inf | 1.6252 | 1925 | 0.7397 |
| 50 | SHORT + ADX>22.5 + RSI 30-45 | inf | 1.6238 | 1777 | 0.7248 |
| 51 | SHORT + RSI 30-45 + Confidence>5.68 | inf | 1.613 | 1069 | 0.6351 |
| 52 | SHORT + EMA20<EMA50 + RSI<34 | inf | 1.6033 | 766 | 0.7987 |
| 53 | SHORT + EMA20<EMA50 + MACD- | inf | 1.5952 | 4787 | 0.7416 |
| 54 | SHORT + RSI 45-55 + Confidence>5.68 | inf | 1.5872 | 1310 | 0.6114 |
| 55 | SHORT + RSI<34 | inf | 1.5865 | 783 | 0.7993 |
| 56 | SHORT + Pattern=g31 + RSI<34 | inf | 1.5865 | 783 | 0.7993 |
| 57 | SHORT + MACD- + RSI<34 | inf | 1.5865 | 783 | 0.7993 |
| 58 | SHORT + EMA20<EMA50 + RSI 30-45 | inf | 1.5824 | 2624 | 0.7279 |
| 59 | SHORT + EMA20<EMA50 + RSI 45-55 | inf | 1.5754 | 2004 | 0.731 |
| 60 | SHORT + EMA20<EMA50 | inf | 1.5606 | 5600 | 0.7361 |
| 61 | SHORT + Pattern=g31 + EMA20<EMA50 | inf | 1.5606 | 5600 | 0.7361 |
| 62 | SHORT + ADX>22.5 + Confidence>5.68 | inf | 1.5521 | 1601 | 0.6496 |
| 63 | SHORT + ATR%<0.6 + EMA20<EMA50 | inf | 1.5476 | 5558 | 0.7341 |
| 64 | SHORT + ATR%<0.6 + RSI<34 | inf | 1.5428 | 770 | 0.7959 |
| 65 | SHORT + MACD- | inf | 1.5331 | 5408 | 0.7241 |
| 66 | SHORT + Pattern=g31 + MACD- | inf | 1.5331 | 5408 | 0.7241 |
| 67 | ATR%<0.6 + ADX>22.5 + Hour=20 | inf | 1.5237 | 877 | 0.7079 |
| 68 | SHORT + ATR%<0.6 + MACD- | inf | 1.5223 | 5359 | 0.7216 |
| 69 | ADX>22.5 + Hour=20 | inf | 1.5214 | 887 | 0.7033 |
| 70 | Pattern=g31 + ADX>22.5 + Hour=20 | inf | 1.5214 | 887 | 0.7033 |
| 71 | SHORT + Confidence<=5.68 + Weekday=Fri | inf | 1.5048 | 1440 | 0.8389 |
| 72 | SHORT + MACD- + ATR% 0.15-0.234 | inf | 1.4976 | 1814 | 0.6951 |
| 73 | SHORT + MACD- + RSI 30-45 | inf | 1.4928 | 2889 | 0.7023 |
| 74 | SHORT + ATR%<0.6 + Weekday=Fri | inf | 1.4791 | 1859 | 0.8101 |
| 75 | ATR%<0.6 + EMA20<EMA50 + Hour=20 | inf | 1.4668 | 1167 | 0.5457 |
| 76 | SHORT + Weekday=Fri | inf | 1.4656 | 1891 | 0.8102 |
| 77 | SHORT + Pattern=g31 + Weekday=Fri | inf | 1.4656 | 1891 | 0.8102 |
| 78 | EMA20<EMA50 + Hour=20 | inf | 1.4656 | 1177 | 0.5436 |
| 79 | Pattern=g31 + EMA20<EMA50 + Hour=20 | inf | 1.4656 | 1177 | 0.5436 |
| 80 | SHORT + EMA20<EMA50 + ATR% 0.15-0.234 | inf | 1.4588 | 1850 | 0.7189 |
| 81 | ATR%<0.6 + Hour=20 | inf | 1.4512 | 1700 | 0.5906 |
| 82 | ATR%<0.6 + Pattern=g31 + Hour=20 | inf | 1.4512 | 1700 | 0.5906 |
| 83 | SHORT + RSI 30-45 | inf | 1.4459 | 3085 | 0.6927 |
| 84 | SHORT + Pattern=g31 + RSI 30-45 | inf | 1.4459 | 3085 | 0.6927 |
| 85 | SHORT + ATR%<0.6 + RSI 30-45 | inf | 1.4359 | 3053 | 0.6905 |
| 86 | ATR%<0.6 + Hour=19 | inf | 1.4322 | 1389 | 0.5565 |
| 87 | ATR%<0.6 + Pattern=g31 + Hour=19 | inf | 1.4322 | 1389 | 0.5565 |
| 88 | SHORT + Confidence>5.68 | inf | 1.4279 | 3418 | 0.598 |
| 89 | SHORT + Pattern=g31 + Confidence>5.68 | inf | 1.4279 | 3418 | 0.598 |
| 90 | SHORT + ATR%<0.6 + Confidence>5.68 | inf | 1.422 | 3403 | 0.5971 |
| 91 | SHORT + EMA20<EMA50 + ADX<=22.5 | inf | 1.4008 | 2737 | 0.6818 |
| 92 | SHORT + ATR% 0.15-0.234 + Confidence>5.68 | inf | 1.3974 | 1110 | 0.589 |
| 93 | SHORT + ADX>22.5 + RSI 45-55 | inf | 1.3949 | 1284 | 0.6737 |
| 94 | SHORT + MACD- + ADX<=22.5 | inf | 1.3895 | 2530 | 0.6735 |
| 95 | SHORT + ADX>22.5 | inf | 1.3868 | 4618 | 0.6767 |
| 96 | SHORT + Pattern=g31 + ADX>22.5 | inf | 1.3868 | 4618 | 0.6767 |
| 97 | SHORT + ATR%<0.6 + ADX>22.5 | inf | 1.3826 | 4539 | 0.6761 |
| 98 | SHORT + ADX<=22.5 + ATR% 0.15-0.234 | inf | 1.3798 | 1819 | 0.6625 |
| 99 | Hour=20 | inf | 1.3779 | 1800 | 0.5594 |
| 100 | Pattern=g31 + Hour=20 | inf | 1.3779 | 1800 | 0.5594 |

## 4. TOP losing setups (100)

| # | setup | PF | EV | n |
|---:|---|---:|---:|---:|
| 1 | Gate=INSUFFICIENT_HISTORY + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 2 | Funding+ + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 3 | Regime=RANGE + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 4 | Gate=INSUFFICIENT_HISTORY + Funding+ + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 5 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 6 | Gate=INSUFFICIENT_HISTORY + Pattern=g31 + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 7 | Gate=INSUFFICIENT_HISTORY + Confidence>5.68 + LONG | 0.13 | -39.6695 | 25 |
| 8 | Funding+ + Regime=RANGE + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 9 | Funding+ + Pattern=g31 + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 10 | Funding+ + Confidence>5.68 + LONG | 0.13 | -39.6695 | 25 |
| 11 | Regime=RANGE + Pattern=g31 + Confidence>5.68 | 0.13 | -39.6695 | 25 |
| 12 | Regime=RANGE + Confidence>5.68 + LONG | 0.13 | -39.6695 | 25 |
| 13 | Gate=INSUFFICIENT_HISTORY + EMA20<EMA50 | 0.28 | -41.4275 | 34 |
| 14 | Gate=INSUFFICIENT_HISTORY + Funding+ + EMA20<EMA50 | 0.28 | -41.4275 | 34 |
| 15 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + EMA20<EMA50 | 0.28 | -41.4275 | 34 |
| 16 | Gate=INSUFFICIENT_HISTORY + ATR%<0.6 + EMA20<EMA50 | 0.28 | -41.4275 | 34 |
| 17 | Gate=INSUFFICIENT_HISTORY + Pattern=g31 + EMA20<EMA50 | 0.28 | -41.4275 | 34 |
| 18 | Gate=INSUFFICIENT_HISTORY + EMA20<EMA50 + LONG | 0.28 | -41.4275 | 34 |
| 19 | Funding+ + EMA20<EMA50 + MACD- | 0.32 | -33.3501 | 25 |
| 20 | Regime=RANGE + EMA20<EMA50 + MACD- | 0.32 | -33.3501 | 25 |
| 21 | Gate=INSUFFICIENT_HISTORY + ADX<=22.5 | 0.34 | -36.8117 | 29 |
| 22 | Gate=INSUFFICIENT_HISTORY + Funding+ + ADX<=22.5 | 0.34 | -36.8117 | 29 |
| 23 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + ADX<=22.5 | 0.34 | -36.8117 | 29 |
| 24 | Gate=INSUFFICIENT_HISTORY + ATR%<0.6 + ADX<=22.5 | 0.34 | -36.8117 | 29 |
| 25 | Gate=INSUFFICIENT_HISTORY + Pattern=g31 + ADX<=22.5 | 0.34 | -36.8117 | 29 |
| 26 | Gate=INSUFFICIENT_HISTORY + ADX<=22.5 + LONG | 0.34 | -36.8117 | 29 |
| 27 | Gate=INSUFFICIENT_HISTORY + EMA20<EMA50 + ADX<=22.5 | 0.34 | -39.035 | 27 |
| 28 | Funding+ + EMA20<EMA50 | 0.36 | -36.7215 | 37 |
| 29 | Regime=RANGE + EMA20<EMA50 | 0.36 | -36.7215 | 37 |
| 30 | Funding+ + Regime=RANGE + EMA20<EMA50 | 0.36 | -36.7215 | 37 |
| 31 | Funding+ + ATR%<0.6 + EMA20<EMA50 | 0.36 | -36.7215 | 37 |
| 32 | Funding+ + Pattern=g31 + EMA20<EMA50 | 0.36 | -36.7215 | 37 |
| 33 | Funding+ + EMA20<EMA50 + LONG | 0.36 | -36.7215 | 37 |
| 34 | Regime=RANGE + ATR%<0.6 + EMA20<EMA50 | 0.36 | -36.7215 | 37 |
| 35 | Regime=RANGE + Pattern=g31 + EMA20<EMA50 | 0.36 | -36.7215 | 37 |
| 36 | Regime=RANGE + EMA20<EMA50 + LONG | 0.36 | -36.7215 | 37 |
| 37 | OI+ | 0.36 | -27.6198 | 25 |
| 38 | Gate=INSUFFICIENT_HISTORY + OI+ | 0.36 | -27.6198 | 25 |
| 39 | Funding+ + OI+ | 0.36 | -27.6198 | 25 |
| 40 | Regime=RANGE + OI+ | 0.36 | -27.6198 | 25 |
| 41 | OI+ + Pattern=g31 | 0.36 | -27.6198 | 25 |
| 42 | OI+ + LONG | 0.36 | -27.6198 | 25 |
| 43 | Gate=INSUFFICIENT_HISTORY + Funding+ + OI+ | 0.36 | -27.6198 | 25 |
| 44 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + OI+ | 0.36 | -27.6198 | 25 |
| 45 | Gate=INSUFFICIENT_HISTORY + OI+ + Pattern=g31 | 0.36 | -27.6198 | 25 |
| 46 | Gate=INSUFFICIENT_HISTORY + OI+ + LONG | 0.36 | -27.6198 | 25 |
| 47 | Funding+ + Regime=RANGE + OI+ | 0.36 | -27.6198 | 25 |
| 48 | Funding+ + OI+ + Pattern=g31 | 0.36 | -27.6198 | 25 |
| 49 | Funding+ + OI+ + LONG | 0.36 | -27.6198 | 25 |
| 50 | Regime=RANGE + OI+ + Pattern=g31 | 0.36 | -27.6198 | 25 |
| 51 | Regime=RANGE + OI+ + LONG | 0.36 | -27.6198 | 25 |
| 52 | OI+ + Pattern=g31 + LONG | 0.36 | -27.6198 | 25 |
| 53 | Gate=INSUFFICIENT_HISTORY + ATR%<0.6 | 0.37 | -29.7077 | 43 |
| 54 | Gate=INSUFFICIENT_HISTORY + Funding+ + ATR%<0.6 | 0.37 | -29.7077 | 43 |
| 55 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + ATR%<0.6 | 0.37 | -29.7077 | 43 |
| 56 | Gate=INSUFFICIENT_HISTORY + ATR%<0.6 + Pattern=g31 | 0.37 | -29.7077 | 43 |
| 57 | Gate=INSUFFICIENT_HISTORY + ATR%<0.6 + LONG | 0.37 | -29.7077 | 43 |
| 58 | Gate=INSUFFICIENT_HISTORY | 0.37 | -28.3874 | 45 |
| 59 | Gate=INSUFFICIENT_HISTORY + Funding+ | 0.37 | -28.3874 | 45 |
| 60 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE | 0.37 | -28.3874 | 45 |
| 61 | Gate=INSUFFICIENT_HISTORY + Pattern=g31 | 0.37 | -28.3874 | 45 |
| 62 | Gate=INSUFFICIENT_HISTORY + LONG | 0.37 | -28.3874 | 45 |
| 63 | Gate=INSUFFICIENT_HISTORY + Funding+ + Regime=RANGE | 0.37 | -28.3874 | 45 |
| 64 | Gate=INSUFFICIENT_HISTORY + Funding+ + Pattern=g31 | 0.37 | -28.3874 | 45 |
| 65 | Gate=INSUFFICIENT_HISTORY + Funding+ + LONG | 0.37 | -28.3874 | 45 |
| 66 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + Pattern=g31 | 0.37 | -28.3874 | 45 |
| 67 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + LONG | 0.37 | -28.3874 | 45 |
| 68 | Gate=INSUFFICIENT_HISTORY + Pattern=g31 + LONG | 0.37 | -28.3874 | 45 |
| 69 | Gate=INSUFFICIENT_HISTORY + MACD- | 0.39 | -25.9981 | 25 |
| 70 | Gate=INSUFFICIENT_HISTORY + Funding+ + MACD- | 0.39 | -25.9981 | 25 |
| 71 | Gate=INSUFFICIENT_HISTORY + Regime=RANGE + MACD- | 0.39 | -25.9981 | 25 |
| 72 | Gate=INSUFFICIENT_HISTORY + ATR%<0.6 + MACD- | 0.39 | -25.9981 | 25 |
| 73 | Gate=INSUFFICIENT_HISTORY + Pattern=g31 + MACD- | 0.39 | -25.9981 | 25 |
| 74 | Gate=INSUFFICIENT_HISTORY + MACD- + LONG | 0.39 | -25.9981 | 25 |
| 75 | Funding+ + MACD- | 0.41 | -26.8994 | 27 |
| 76 | Regime=RANGE + MACD- | 0.41 | -26.8994 | 27 |
| 77 | Funding+ + Regime=RANGE + MACD- | 0.41 | -26.8994 | 27 |
| 78 | Funding+ + ATR%<0.6 + MACD- | 0.41 | -26.8994 | 27 |
| 79 | Funding+ + Pattern=g31 + MACD- | 0.41 | -26.8994 | 27 |
| 80 | Funding+ + MACD- + LONG | 0.41 | -26.8994 | 27 |
| 81 | Regime=RANGE + ATR%<0.6 + MACD- | 0.41 | -26.8994 | 27 |
| 82 | Regime=RANGE + Pattern=g31 + MACD- | 0.41 | -26.8994 | 27 |
| 83 | Regime=RANGE + MACD- + LONG | 0.41 | -26.8994 | 27 |
| 84 | Funding+ + ADX<=22.5 | 0.41 | -32.9744 | 33 |
| 85 | Regime=RANGE + ADX<=22.5 | 0.41 | -32.9744 | 33 |
| 86 | Funding+ + Regime=RANGE + ADX<=22.5 | 0.41 | -32.9744 | 33 |
| 87 | Funding+ + ATR%<0.6 + ADX<=22.5 | 0.41 | -32.9744 | 33 |
| 88 | Funding+ + Pattern=g31 + ADX<=22.5 | 0.41 | -32.9744 | 33 |
| 89 | Funding+ + ADX<=22.5 + LONG | 0.41 | -32.9744 | 33 |
| 90 | Regime=RANGE + ATR%<0.6 + ADX<=22.5 | 0.41 | -32.9744 | 33 |
| 91 | Regime=RANGE + Pattern=g31 + ADX<=22.5 | 0.41 | -32.9744 | 33 |
| 92 | Regime=RANGE + ADX<=22.5 + LONG | 0.41 | -32.9744 | 33 |
| 93 | Funding+ + EMA20<EMA50 + ADX<=22.5 | 0.43 | -33.4702 | 30 |
| 94 | Regime=RANGE + EMA20<EMA50 + ADX<=22.5 | 0.43 | -33.4702 | 30 |
| 95 | Funding+ + ATR%<0.6 | 0.44 | -26.3482 | 48 |
| 96 | Regime=RANGE + ATR%<0.6 | 0.44 | -26.3482 | 48 |
| 97 | Funding+ + Regime=RANGE + ATR%<0.6 | 0.44 | -26.3482 | 48 |
| 98 | Funding+ + ATR%<0.6 + Pattern=g31 | 0.44 | -26.3482 | 48 |
| 99 | Funding+ + ATR%<0.6 + LONG | 0.44 | -26.3482 | 48 |
| 100 | Regime=RANGE + ATR%<0.6 + Pattern=g31 | 0.44 | -26.3482 | 48 |

## 5. Minimal rule sets

- `Weekday=Tue` → PF inf  EV 1.6255  n=2120  conf=0.5566
- `Hour=20` → PF inf  EV 1.3779  n=1800  conf=0.5594
- `Hour=19` → PF inf  EV 1.3588  n=1480  conf=0.5338
- `SHORT` → PF inf  EV 1.2797  n=9502  conf=0.6503
- `RSI<34` → PF inf  EV 0.9763  n=1398  conf=0.5171
- `RSI 55-70` → PF inf  EV 0.9604  n=3771  conf=0.5184
- `Weekday=Wed` → PF inf  EV 0.9357  n=2000  conf=0.613
- `SHORT + EMA20>EMA50` → PF inf  EV 0.8765  n=3902  conf=0.5272
- `Hour=8` → PF inf  EV 0.8726  n=1540  conf=0.5143
- `Weekday=Fri` → PF inf  EV 0.7474  n=5800  conf=0.5693
- `Hour=21` → PF inf  EV 0.7339  n=1780  conf=0.6693
- `Weekday=Sat` → PF inf  EV 0.7116  n=5960  conf=0.5168

## 6. Forbidden setups (BLOCK)

- BLOCK `Gate=INSUFFICIENT_HISTORY + ATR%<0.6` → PF 0.37  EV -29.7077  n=43
- BLOCK `Gate=INSUFFICIENT_HISTORY + Funding+ + ATR%<0.6` → PF 0.37  EV -29.7077  n=43
- BLOCK `Gate=INSUFFICIENT_HISTORY + Regime=RANGE + ATR%<0.6` → PF 0.37  EV -29.7077  n=43
- BLOCK `Gate=INSUFFICIENT_HISTORY + ATR%<0.6 + Pattern=g31` → PF 0.37  EV -29.7077  n=43
- BLOCK `Gate=INSUFFICIENT_HISTORY + ATR%<0.6 + LONG` → PF 0.37  EV -29.7077  n=43
- BLOCK `Gate=INSUFFICIENT_HISTORY` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Funding+` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Regime=RANGE` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Pattern=g31` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + LONG` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Funding+ + Regime=RANGE` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Funding+ + Pattern=g31` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Funding+ + LONG` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Regime=RANGE + Pattern=g31` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Regime=RANGE + LONG` → PF 0.37  EV -28.3874  n=45
- BLOCK `Gate=INSUFFICIENT_HISTORY + Pattern=g31 + LONG` → PF 0.37  EV -28.3874  n=45
- BLOCK `Funding+ + ATR%<0.6` → PF 0.44  EV -26.3482  n=48
- BLOCK `Regime=RANGE + ATR%<0.6` → PF 0.44  EV -26.3482  n=48
- BLOCK `Funding+ + Regime=RANGE + ATR%<0.6` → PF 0.44  EV -26.3482  n=48
- BLOCK `Funding+ + ATR%<0.6 + Pattern=g31` → PF 0.44  EV -26.3482  n=48

## 7. Top 20 coins

- **MANTA** n=958 PF=inf EV=0.8057  | best: LONG + RSI<45 + H18 PF=inf n=8; SHORT + ATR_low + H7 PF=inf n=9  | worst: LONG + H2 PF=0.00 n=10; LONG + ATR_low + H4 PF=0.00 n=10
- **MATIC** n=958 PF=0.00 EV=0.0  | best: -  | worst: LONG + H18 PF=0.00 n=77; LONG PF=0.00 n=958
- **NEAR** n=958 PF=2.57 EV=0.768  | best: SHORT + RSI<45 + ATR_low + H18 PF=inf n=18; SHORT + H9 PF=inf n=10  | worst: LONG + RSI<34 + H18 PF=0.00 n=10; LONG + RSI<45 + ATR_low + H20 PF=0.00 n=17
- **OP** n=958 PF=4.47 EV=1.3018  | best: SHORT + ATR_low + H6 PF=inf n=20; SHORT + ATR_low + H20 PF=inf n=32  | worst: LONG + RSI<34 + ATR_low + H18 PF=0.00 n=12; LONG + ATR_low + H19 PF=0.00 n=24
- **PEPE** n=958 PF=inf EV=1.017  | best: SHORT + RSI<34 + H21 PF=inf n=10; SHORT + H19 PF=inf n=15  | worst: LONG + H9 PF=0.00 n=17; LONG + RSI<34 + H20 PF=0.00 n=10
- **SOL** n=958 PF=12.51 EV=0.5934  | best: SHORT + ATR_low + H18 PF=inf n=20; SHORT + RSI<45 + ATR_low + H19 PF=inf n=23  | worst: LONG + ATR_low + H20 PF=0.00 n=35; LONG + ATR_low + H21 PF=0.00 n=33
- **SUI** n=958 PF=3.35 EV=0.3788  | best: SHORT + ATR_low + H18 PF=inf n=16; SHORT + RSI<45 + ATR_low + H19 PF=inf n=24  | worst: LONG + RSI<45 + ATR_low + H18 PF=0.00 n=17; LONG + ATR_low + H19 PF=0.00 n=23
- **TON** n=958 PF=0.00 EV=0.0  | best: -  | worst: LONG + RSI>70 + ATR_low + H18 PF=0.00 n=77; LONG PF=0.00 n=958
- **WIF** n=958 PF=21.15 EV=0.9652  | best: SHORT + RSI<45 + H19 PF=inf n=9; SHORT + RSI<45 + ATR_low + H4 PF=inf n=10  | worst: LONG + RSI<45 + ATR_low + H18 PF=0.00 n=18; LONG + ATR_low + H19 PF=0.00 n=15
- **XRP** n=958 PF=17.59 EV=0.727  | best: SHORT + ATR_low + H19 PF=inf n=12; SHORT + ATR_low + H18 PF=inf n=13  | worst: LONG + ATR_low + H20 PF=0.00 n=16; LONG + RSI<45 + ATR_low + H2 PF=0.00 n=10
- **ADA** n=958 PF=inf EV=1.0369  | best: LONG + ATR_low + H11 PF=inf n=27; LONG + ATR_low + H7 PF=inf n=25  | worst: LONG + ATR_low + H20 PF=0.00 n=12; LONG + RSI<45 + H2 PF=0.00 n=10
- **APT** n=958 PF=44.36 EV=1.1331  | best: SHORT + RSI<45 + H20 PF=inf n=10; SHORT + ATR_low + H18 PF=inf n=15  | worst: LONG + RSI<45 + ATR_low + H20 PF=0.00 n=24; LONG + ATR_low + H21 PF=0.00 n=17
- **ARB** n=958 PF=1.51 EV=0.3468  | best: SHORT + ATR_low + H19 PF=inf n=18; SHORT + RSI<45 + ATR_low + H20 PF=inf n=23  | worst: LONG + ATR_low + H21 PF=0.00 n=18; LONG + ATR_low + H2 PF=0.00 n=10
- **AVAX** n=958 PF=7.34 EV=0.5817  | best: SHORT + RSI<34 + H19 PF=inf n=12; SHORT + RSI<45 + ATR_low + H20 PF=inf n=16  | worst: LONG + ATR_low + H20 PF=0.00 n=24; LONG + ATR_low + H2 PF=0.00 n=10
- **BNB** n=958 PF=15.48 EV=0.6353  | best: LONG + ATR_low + H8 PF=inf n=25; LONG + RSI<45 + ATR_low + H9 PF=inf n=14  | worst: LONG + ATR_low + H20 PF=0.00 n=39; LONG + RSI<45 + ATR_low + H21 PF=0.00 n=20
- **BTC** n=958 PF=inf EV=0.8895  | best: SHORT + ATR_low + H18 PF=inf n=24; SHORT + ATR_low + H6 PF=inf n=8  | worst: LONG + RSI<45 + ATR_low + H2 PF=0.00 n=10; LONG + ATR_low + H4 PF=0.00 n=10
- **DOGE** n=958 PF=5.41 EV=0.3867  | best: SHORT + ATR_low + H18 PF=inf n=19; SHORT + ATR_low + H20 PF=inf n=34  | worst: LONG + RSI<45 + ATR_low + H18 PF=0.00 n=10; LONG + ATR_low + H19 PF=0.00 n=36
- **ETH** n=958 PF=inf EV=1.5033  | best: LONG + RSI<45 + ATR_low + H18 PF=inf n=12; LONG + ATR_low + H18 PF=inf n=16  | worst: LONG + RSI<45 + H20 PF=0.00 n=10; LONG + RSI<45 + ATR_low + H21 PF=0.00 n=20
- **INJ** n=958 PF=5.53 EV=1.1605  | best: SHORT + ATR_low + H6 PF=inf n=10; SHORT + RSI<45 + ATR_low + H6 PF=inf n=10  | worst: LONG + ATR_low + H19 PF=0.00 n=26; LONG + ATR_low + H20 PF=0.00 n=10
- **LINK** n=958 PF=inf EV=1.7213  | best: LONG + RSI<45 + ATR_low + H18 PF=inf n=18; SHORT + ATR_low + H18 PF=inf n=15  | worst: LONG + ATR_low + H18 PF=0.00 n=21; SHORT + ATR_low + H9 PF=0.00 n=10

## Integrity

- research_only: True
- paper_unchanged: True
- execution_unchanged: True
- strategy_unchanged: True
- gate_unchanged: True
- optimizer_unchanged: True
- brain_unchanged: True
