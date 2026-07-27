# Next tasks

Cursor updates this file after each task.

## IN PROGRESS

- (none)

## TODO

- Kill duplicate `shock-paper-core` PIDs if two processes hold `market_events.db`
- Mac mini: `git pull`, restart shock-paper, run `doctor` + `self-test`
- Optional: extend `watch` with per-detector accept counters from in-memory metrics (requires IPC or ops table)

## BLOCKERS

- None

## DONE

- [x] Runtime health: `doctor`, `watch`, `self-test` (2026-07-27)
- [x] SQLite lock fix for shadow/near-miss batching (2026-07-26)
- [x] Adaptive shadow profile `adaptive_v1` (2026-07-26)
