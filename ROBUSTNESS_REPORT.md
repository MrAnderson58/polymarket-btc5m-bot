# ROBUSTNESS_REPORT

## Stress
- baseline: pnl=14843.877 wr=0.454048 sharpe=20.060534
- double_fees: pnl=11002.877 wr=0.428066 sharpe=14.869672
- triple_fees: pnl=9082.377 wr=0.414788 sharpe=12.274241
- random_slippage: pnl=10031.599815 wr=0.421765 sharpe=13.558688
- execution_delay: pnl=14804.7353 wr=0.453996 sharpe=20.03458
- missed_trades: pnl=12556.2719 wr=0.387503 sharpe=18.01007
- half_liquidity: pnl=7421.9385 wr=0.454048 sharpe=20.060534
- gap_losses: pnl=11159.877 wr=0.433845 sharpe=14.882114

survive_rate=1.0

## Regimes
```json
{
  "bull": 2525,
  "bear": 6195,
  "range": 10429,
  "mixed": 56,
  "all": 19205
}
```
fragile=False

## Leave-One-Coin-Out
sign_flips=0 fragile=False

## Leave-One-Month-Out
sign_flips=0 fragile=False
