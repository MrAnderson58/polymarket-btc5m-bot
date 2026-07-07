# Dense Era Boundary

Identified from `v4_shadow_observations` only (not git deploy time).

## Per-market thresholds

- obs_count ≥ 60
- median_gap ≤ 5 sec
- coverage_span ≥ 240 sec
- market completed (seconds_left ≤ 15 or span ≥ 240)

## Stable boundary

First `window_start_ts` where 5 consecutive completed markets pass all thresholds.

## Commands

```bash
python -m bot.research.strategy_simulator dense-era

python -m bot.research.strategy_simulator discover \
  --market-start-ts WS --min-obs-per-market 60 \
  --max-median-gap 5 --completed-only
```

Run `dense-era` on production DB to obtain `WS`.
