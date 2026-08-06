# REPLAY_RECOVERY

_Replay Recovery Investigation V1 — research only. Replay/Decision/Gate unchanged._

- runtime: **1.562s**
- replay_floor: **0.5**
- candidates: **19205**
- rejected_by_replay: **19155**
- missed_winners (FN): **8702**
- saved_losers (TN): **29**
- recoverable EV (lost winners): **16940.4952**
- protected EV (blocked losers): **831.9049**
- net Replay EV (protected − recoverable): **-16108.5903**
- largest replay mistake: trade `19201` WIF pnl=77.7242 replay=None reason=replay_missing

## Confusion matrix
- TP accepted winners: 18
- FP accepted losers: 32
- FN missed winners: 8702
- TN saved losers: 10453

## Why Replay rejected (histogram)

| Reason | n |
|--------|--:|
| replay_missing | 19155 |

## Minimal floor proposals (do not apply)
- baseline floor=0.5 recoverable_ev=16940.4952
- best feasible: 0.45 ev_recovered=0.0 dd_increase_pct=0.0

research_only=true observe_only=true
