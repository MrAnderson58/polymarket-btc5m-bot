# REPLAY_ERRORS

## Missed winners (false rejects) — top by PnL

| trade_id | symbol | pnl | replay | reason | regime |
|---------:|--------|----:|-------:|--------|--------|
| 19201 | WIF | 77.7242 | None | replay_missing | RANGE |
| 19197 | OP | 53.9405 | None | replay_missing | RANGE |
| 19187 | WIF | 39.1417 | None | replay_missing | RANGE |
| 19204 | APT | 32.7752 | None | replay_missing | RANGE |
| 19199 | SOL | 32.0504 | None | replay_missing | RANGE |
| 19183 | OP | 28.0047 | None | replay_missing | RANGE |
| 19195 | LINK | 24.9908 | None | replay_missing | RANGE |
| 19193 | DOGE | 19.2142 | None | replay_missing | RANGE |
| 19169 | OP | 17.955 | None | replay_missing | RANGE |
| 19194 | ETH | 17.9206 | None | replay_missing | RANGE |
| 19200 | SUI | 15.8242 | None | replay_missing | RANGE |
| 803 | ARB | 13.6992 | None | replay_missing | None |
| 823 | ARB | 13.6992 | None | replay_missing | None |
| 843 | ARB | 13.6992 | None | replay_missing | None |
| 863 | ARB | 13.6992 | None | replay_missing | None |
| 883 | ARB | 13.6992 | None | replay_missing | None |
| 903 | ARB | 13.6992 | None | replay_missing | None |
| 923 | ARB | 13.6992 | None | replay_missing | None |
| 943 | ARB | 13.6992 | None | replay_missing | None |
| 963 | ARB | 13.6992 | None | replay_missing | None |
| 983 | ARB | 13.6992 | None | replay_missing | None |
| 1814 | OP | 13.4948 | None | replay_missing | None |
| 1834 | OP | 13.4948 | None | replay_missing | None |
| 1854 | OP | 13.4948 | None | replay_missing | None |
| 2014 | OP | 13.2773 | None | replay_missing | None |
| 2034 | OP | 13.2773 | None | replay_missing | None |
| 2054 | OP | 13.2773 | None | replay_missing | None |
| 2074 | OP | 13.2773 | None | replay_missing | None |
| 2094 | OP | 13.2773 | None | replay_missing | None |
| 2114 | OP | 13.2773 | None | replay_missing | None |
| 2134 | OP | 13.2773 | None | replay_missing | None |
| 2154 | OP | 13.2773 | None | replay_missing | None |
| 2174 | OP | 13.1883 | None | replay_missing | None |
| 2194 | OP | 13.1883 | None | replay_missing | None |
| 603 | ARB | 12.5867 | None | replay_missing | None |
| 623 | ARB | 12.5867 | None | replay_missing | None |
| 643 | ARB | 12.5867 | None | replay_missing | None |
| 663 | ARB | 12.5867 | None | replay_missing | None |
| 683 | ARB | 12.5867 | None | replay_missing | None |
| 703 | ARB | 12.5867 | None | replay_missing | None |

## Feature deltas (missed winners − saved losers)

| Feature | mean_winner | mean_loser | Δ |
|---------|------------:|-----------:|--:|
| atr | 93.9875 | 93.358621 | 0.628879 |
| funding | 50.68125 | 50.658621 | 0.022629 |
| oi_delta | -250499.128688 | -481932.145207 | 231433.016519 |
| timeline_similarity | 0.665034 | 0.596207 | 0.068827 |
| fingerprint_similarity | 0.336004 | 0.062141 | 0.273863 |
| dna | 0.814671 | 0.8207 | -0.006029 |
| brain | 0.012453 | 0.15 | -0.137547 |
| edge | 0.45 | 0.45 | 0.0 |
| confidence | 0.425558 | 0.331321 | 0.094237 |
| historical_wr | 58.086667 | 46.435862 | 11.650805 |
| historical_ev | 13.662275 | 15.190048 | -1.527773 |
| historical_pf | 727.25839 | 39.722948 | 687.535442 |

## Regime / coin / session
```json
{
  "regime": {
    "key": "regime",
    "rows": [
      {
        "value": "unknown",
        "n_winners": 8686,
        "n_losers": 0,
        "winner_share": 0.9982,
        "loser_share": 0.0
      },
      {
        "value": "RANGE",
        "n_winners": 16,
        "n_losers": 29,
        "winner_share": 0.0018,
        "loser_share": 1.0
      }
    ]
  },
  "coin": {
    "key": "symbol",
    "rows": [
      {
        "value": "WIF",
        "n_winners": 598,
        "n_losers": 0,
        "winner_share": 0.0687,
        "loser_share": 0.0
      },
      {
        "value": "OP",
        "n_winners": 595,
        "n_losers": 0,
        "winner_share": 0.0684,
        "loser_share": 0.0
      },
      {
        "value": "APT",
        "n_winners": 539,
        "n_losers": 3,
        "winner_share": 0.0619,
        "loser_share": 0.1034
      },
      {
        "value": "AVAX",
        "n_winners": 526,
        "n_losers": 4,
        "winner_share": 0.0604,
        "loser_share": 0.1379
      },
      {
        "value": "PEPE",
        "n_winners": 525,
        "n_losers": 2,
        "winner_share": 0.0603,
        "loser_share": 0.069
      },
      {
        "value": "INJ",
        "n_winners": 510,
        "n_losers": 2,
        "winner_share": 0.0586,
        "loser_share": 0.069
      },
      {
        "value": "LINK",
        "n_winners": 499,
        "n_losers": 2,
        "winner_share": 0.0573,
        "loser_share": 0.069
      },
      {
        "value": "ETH",
        "n_winners": 497,
        "n_losers": 2,
        "winner_share": 0.0571,
        "loser_share": 0.069
      },
      {
        "value": "XRP",
        "n_winners": 473,
        "n_losers": 2,
        "winner_share": 0.0544,
        "loser_share": 0.069
      },
      {
        "value": "NEAR",
        "n_winners": 474,
        "n_losers": 1,
        "winner_share": 0.0545,
        "loser_share": 0.0345
      },
      {
        "value": "BNB",
        "n_winners": 471,
        "n_losers": 0,
        "winner_share": 0.0541,
        "loser_share": 0.0
      },
      {
        "value": "ADA",
        "n_winners": 466,
        "n_losers": 4,
        "winner_share": 0.0536,
        "loser_share": 0.1379
      }
    ]
  },
  "session": {
    "key": "session",
    "rows": [
      {
        "value": "us",
        "n_winners": 3978,
        "n_losers": 29,
        "winner_share": 0.4571,
        "loser_share": 1.0
      },
      {
        "value": "europe",
        "n_winners": 3797,
        "n_losers": 0,
        "winner_share": 0.4363,
        "loser_share": 0.0
      },
      {
        "value": "asia",
        "n_winners": 927,
        "n_losers": 0,
        "winner_share": 0.1065,
        "loser_share": 0.0
      }
    ]
  }
}
```
