# Pipeline Flow Audit — Signal Mathematics Recovery

_Window=24h | processing_time_ms=98.8_

```
Market → Collector → Candidate → Features → Gate → S55 → S42 → Close → Research → Feature Store → ML
```

## Flow

### Market / Snapshots
- input_rows: `None`
- output_rows: `2567`
- window_rows: `652`
- drop_%: `None`
- notes: candles=32734

### Collector → Candidates (G31)
- input_rows: `652`
- output_rows: `51240`
- window_rows: `13060`
- drop_%: `None`
- notes: rejected_in_window=13060

- reject reasons:
  - `No reversal confirmation`: 10494
  - `Volume weak (35)`: 1306
  - `Confidence 7.0 < 7.5`: 260
  - `Confidence 7.1 < 7.5`: 182
  - `Confidence 7.2 < 7.5`: 140
  - `Volume weak (39)`: 118
  - `Confidence 6.9 < 7.5`: 109
  - `Volume weak (40)`: 102

### Candidates → S40 Signals
- input_rows: `13060`
- output_rows: `10800`
- window_rows: `3960`
- drop_%: `69.68`
- notes: None

### Signals → Features (S55) **CRITICAL BOTTLENECK**
- input_rows: `3960`
- output_rows: `150`
- window_rows: `100`
- drop_%: `97.47`
- notes: S55 includes blocked attempts (paper_trade_id NULL)

- reject reasons:
  - `NEGATIVE_EXPECTANCY`: 100

### Gate (kept vs logged) **CRITICAL BOTTLENECK**
- input_rows: `150`
- output_rows: `5`
- window_rows: `None`
- drop_%: `96.67`
- notes: ALLOWED/REGIME_EXPLORE/EXPLORE counted as pass

- reject reasons:
  - `NEGATIVE_EXPECTANCY`: 100

### Gate → Paper (S42)
- input_rows: `50`
- output_rows: `50`
- window_rows: `0`
- drop_%: `66.67`
- notes: open=0 linked_s55=50

- reject reasons:
  - `NEGATIVE_EXPECTANCY`: 100

### Paper → Closed
- input_rows: `50`
- output_rows: `50`
- window_rows: `0`
- drop_%: `0.0`
- notes: still_open=0

### Closed → Research / Feature Store
- input_rows: `50`
- output_rows: `3`
- window_rows: `None`
- drop_%: `None`
- notes: feature_store_v1 files=3

### Feature Store → ML dataset
- input_rows: `50`
- output_rows: `50`
- window_rows: `None`
- drop_%: `0.0`
- notes: /Users/andrey/polymarket-bot/polymarket-btc5m-bot/research/ml/datasets/v1/training_dataset.csv

## Critical bottlenecks

- **CRITICAL BOTTLENECK** `Candidates(window)` → `S55(window)`: 13060 → 100 (drop 99.23%)
- **CRITICAL BOTTLENECK** `S55(all)` → `Gate-pass`: 150 → 5 (drop 96.67%)
- **CRITICAL BOTTLENECK** `S40(window)` → `S42(window opens)`: 3960 → 0 (drop 100.0%)
