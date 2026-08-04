# PAPER_BOOK_REPORT

_Market Paper Decision A/B/C V1 — research only._

- runtime: **507.677s**
- journal rows: 57615

## BOOK A (paper_baseline)
- Trades: 19205
- WR: 45.4
- PF: 5.8162
- EV: 0.7729
- Sharpe: 0.1448
- MaxDD: -1444.999

## BOOK B (paper_decision)
- Trades: 4111
- WR: 76.23
- PF: —
- EV: 1.5337
- Sharpe: 1.0103
- MaxDD: 0.0

## BOOK C (paper_high_confidence)
- Trades: 0
- WR: None
- PF: —
- EV: None
- Sharpe: None
- MaxDD: None

## Overlap
- A∩B: 4111
- A∩C: 0
- B∩C: 0
- Filtered A−B: 15094
- Filtered A−C: 19205

## Improvement
- B vs A: `{'wr_delta': 30.83, 'pf_delta': None, 'ev_delta': 0.7608, 'sharpe_delta': 0.8655, 'trades_delta': -15094}`
- C vs A: `{'wr_delta': None, 'pf_delta': None, 'ev_delta': None, 'sharpe_delta': None, 'trades_delta': -19205}`
