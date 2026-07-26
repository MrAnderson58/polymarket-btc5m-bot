# Adaptive Shock Profile (Shadow A/B)

## Why

Production `SHOCK_A–E` thresholds (`baseline`) are far above observed 24h volatility
(p99 moves ≈ 0.18–0.31% vs thresholds 1.0–3.0%). The live detector almost never fires.

`adaptive_v1` is a **shadow-only** profile with lower thresholds so we can measure how
often a realistic band would accept events **without** changing paper/live behaviour.

## What does not change

- `SHOCK_THRESHOLDS` in `bot/research/market_events/config.py` (baseline) — unchanged
- Production `detect_shock_triggers` / paper pipeline — still baseline only
- Existing tables (`market_events`, paper runs, near-miss, …) — unchanged

## Profiles

| Profile | Role | Source |
|---------|------|--------|
| `baseline` | Production thresholds | `SHOCK_THRESHOLDS` |
| `adaptive_v1` | Shadow research | `ADAPTIVE_V1_THRESHOLDS` |

Initial `adaptive_v1` values (config only — no hardcodes in detector code):

| Detector | Window | Threshold |
|----------|--------|-----------|
| SHOCK_A | 30s | abs ≥ 0.30% |
| SHOCK_B | 60s | abs ≥ 0.50% |
| SHOCK_C | 180s | abs ≥ 0.80% |
| SHOCK_D | 60s | abs ≥ 0.50% + volume_z ≥ 2 |
| SHOCK_E | 60s | \|ret−BTC\| ≥ 0.30% |

## How it runs

Each `shock-paper` cycle:

1. Baseline diagnostics + shock scan (as before) → may open pending / paper
2. Shadow A/B eval for **both** `baseline` and `adaptive_v1` → writes `market_events_shadow` only

Adaptive accepts **never** enqueue paper, alerts, or lifecycle.

Toggle: `ME_ADAPTIVE_SHADOW_ENABLED=0` disables shadow. Reject rows flush every
`ME_SHADOW_REJECT_FLUSH_SEC` (default 60); accepts persist immediately.

## Table

`market_events_shadow` (idempotent ensure on migrate):

- `id`, `created_at`, `symbol`, `profile_name`, `detector`, `window_sec`
- `return_pct`, `threshold_pct`, `accepted`, `reject_reason`
- `volume_z`, `relative_return_pct`

## Heartbeat

```
shadow adaptive_v1
checked=…
accepted=…
rejected=…
accept_rate=…%
```

## Compare results

```bash
python -m bot.research.market_events shadow-report --days 1
```

Report lists `baseline` vs `adaptive_v1` per detector: checked, accepted, accept_rate,
avg/max return, top symbols.

Dashboard API tab:

```
GET /shadow-profiles?days=1
```

(`tab: "Shadow Profiles"`)

G40 lane report moved to:

```bash
python -m bot.research.market_events g40-shadow-report
```

## How to add a new profile

1. Add thresholds dict in `config.py` (e.g. `ADAPTIVE_V2_THRESHOLDS`)
2. Register in `SHOCK_PROFILE_THRESHOLDS`
3. Include the name in `shadow_profile_report` / dashboard profile list
4. Keep production `SHOCK_THRESHOLDS` untouched until shadow evidence justifies a promotion

## How to interpret

- **High adaptive accept_rate + low baseline** → thresholds were the bottleneck; still require
  quality filters (volume, relative move, gates) before promoting
- **Similar accept_rates** → moves rarely reach even adaptive band; feed/history issue
- **Adaptive accepts with poor post-move MFE** (separate study) → too noisy; raise thresholds
- Promote only after multi-day shadow + paper shadow validation; never hot-swap production
  thresholds from a single session
