# Operating rules — MacBook vs Mac mini

## MacBook (development — Cursor)

1. Implement and test locally.
2. Run:
   ```bash
   python -m bot.research.market_events self-test --skip-network
   python -m bot.research.market_events doctor --skip-network   # optional offline
   pytest tests/test_runtime_health.py tests/test_adaptive_shock_shadow.py -q
   ```
3. Update `PROJECT_OS/PROJECT_HISTORY.md` and `PROJECT_OS/NEXT_TASKS.md` for the completed task.
4. Ship:
   ```bash
   git add .
   git commit -m "<message>"
   git push origin develop-terminal
   ```

## Mac mini (runtime — no git writes)

**Mac mini does NOT commit and does NOT push.**

1. Pull only:
   ```bash
   git pull origin develop-terminal
   ```
2. Migrate if needed:
   ```bash
   python -m bot.research.market_events market-event-migrate
   ```
3. Restart services (supervisor):
   ```bash
   python -m bot.research.market_events stop-all
   python -m bot.research.market_events start-all
   ```
   Or restart individual processes as needed (`status` shows PIDs).
4. Verify:
   ```bash
   python -m bot.research.market_events status
   python -m bot.research.market_events doctor
   python -m bot.research.market_events self-test
   python -m bot.research.market_events shadow-report --days 1
   ```

## Health commands

| Command | Purpose |
|---------|---------|
| `doctor` | One-shot SYSTEM STATUS |
| `watch` | Live refresh every 5s (`--once` for single frame) |
| `self-test` | Smoke test DB + detector + shadow + heartbeat (+ APIs unless `--skip-network`) |
| `status` | Process supervisor list |
| `doctor --platform` | Legacy AI Trading Platform report |

## SQLite

- Prefer one writer per role on `data/market_events.db` (avoid duplicate shock-paper-core).
- See `PROJECT_OS/SQLITE_LOCK_ANALYSIS.md` for lock root cause and shadow batching.
