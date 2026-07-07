# Blackout Recovery — unexpected power loss

Scenario: Mac Mini lost power without graceful shutdown.

---

## 1. Assess git working tree

```bash
cd ~/polymarket-btc5m-bot
git status
git diff --stat
```

Power loss during git operations is rare. If `.git` is corrupt, restore from remote clone.

**Do not** run `git clean -fdx` without review.

---

## 2. Check for incomplete writes

### SQLite (`data/trades.db`)

```bash
sqlite3 data/trades.db "PRAGMA quick_check;"
sqlite3 data/trades.db "PRAGMA integrity_check;"   # slower, more thorough
```

If `quick_check` ≠ `ok`:

1. Stop bot: `./scripts/prod-stop.sh`
2. Copy corrupted file aside: `cp data/trades.db data/trades.db.corrupt.$(date +%s)`
3. Restore from latest `backups/sqlite/trades_*.db` (see BACKUP_RESTORE.md)
4. Re-run `PRAGMA quick_check`

**Do not** delete `data/trades.db` without backup.

### PostgreSQL

```bash
pg_isready
psql -d trading_ai -c "SELECT 1"
```

If Postgres won't start, check macOS logs / `brew services list`. Restore from `backups/postgres/` only if needed.

### Telegram state files

| File | Safe action |
|------|-------------|
| `data/futures_agent_telegram_offset.json` | Keep — resumes polling |
| `data/futures_agent_telegram_last.json` | Keep — diagnostic only |
| `data/futures_agent_telegram_poll.lock` | Remove **only** if no poller running (see below) |

---

## 3. Stale Telegram poll lock

```bash
./scripts/prod-status.sh
```

If telegram-poll **STOPPED** but lock exists:

```bash
python -m bot.research.futures_agent telegram-diagnose
# polling conflict risk: low_lock_available → stale
./scripts/prod-start.sh telegram   # prod-start removes stale lock when safe
```

**Never** delete lock file while poller PID is alive.

---

## 4. Duplicate process check

```bash
./scripts/prod-status.sh
python -m bot.ops healthcheck
```

If duplicates after crash + auto-restart:

```bash
./scripts/prod-stop.sh
sleep 2
./scripts/prod-status.sh
./scripts/prod-start.sh
```

---

## 5. Last V4 observation

```bash
sqlite3 data/trades.db "SELECT MAX(timestamp), datetime(MAX(timestamp), 'unixepoch') FROM v4_shadow_observations;"
python -m bot.ops healthcheck
```

Gap during outage is expected. After restart, age should drop below 120s within 1–2 minutes.

---

## 6. Incomplete current market

The in-progress 5m window may have a density gap across the outage. **This is normal.**

- Do not delete partial market rows
- Wait for **next completed market** before judging collector health
- Run `v4-density` on last 5 **completed** markets only

---

## 7. Collector restart

```bash
./scripts/prod-restart.sh main
```

Confirm config unchanged:

```bash
grep -E '^(ENABLE_V4_SHADOW|V4_POLL_INTERVAL_SEC|MTF_POLL_INTERVAL_SEC|POLL_INTERVAL_SEC)=' .env
```

Expected: `ENABLE_V4_SHADOW=true`, `V4_POLL_INTERVAL_SEC=1`, `MTF_POLL_INTERVAL_SEC=60`, `POLL_INTERVAL_SEC=2`.

**Do not revert** V4 thread decoupling or MTF throttling.

---

## 8. Observation density recovery validation

After **one full 5m market** post-restart:

```bash
python -m bot.collector_diagnostics v4-density --last-markets 5
```

Pass criteria (completed markets):

- median obs/market ≥ 60 (target 89–113)
- median gap ≤ 5 sec (target ~3 sec)

---

## 9. What NOT to delete automatically

| Item | Reason |
|------|--------|
| `data/trades.db` | Primary research + bot state |
| `v4_shadow_observations` rows | Irreplaceable research history |
| `data/futures_agent_telegram_offset.json` | Avoid re-processing flood |
| `.env` | Secrets + config |
| `logs/` | Incident forensics |
| `ss_*` tables | Strategy simulator artifacts |

---

## 10. Escalation path

1. `python -m bot.ops snapshot` → save `reports/ops_snapshot_*.txt`
2. `./scripts/backup-databases.sh`
3. Compare `v4_collector_cycles` / `main_collector_cycles` for slow cycles
4. If density still ~27 obs / 10s gap → config regression or duplicate old binary — check `git log` and `.env`

---

## Quick recovery sequence

```bash
cd ~/polymarket-btc5m-bot && source .venv/bin/activate
sqlite3 data/trades.db "PRAGMA quick_check;"
pg_isready
./scripts/prod-stop.sh
./scripts/prod-start.sh
./scripts/prod-status.sh
python -m bot.ops healthcheck
# wait 5 min
python -m bot.collector_diagnostics v4-density --last-markets 5
```
