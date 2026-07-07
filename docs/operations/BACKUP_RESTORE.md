# Backup and Restore

## Principles

- **SQLite:** use online backup API (`sqlite3.backup` / `connection.backup()`), not `cp` while `bot.main` is writing
- **PostgreSQL:** use `pg_dump`; credentials from `.env` only at runtime
- **Never commit** backups, `.env`, or dumps to git
- **Test restores** on a copy, not production, first

---

## Automated backup script

```bash
cd ~/polymarket-btc5m-bot
./scripts/backup-databases.sh
```

Creates:

- `backups/sqlite/trades_YYYYMMDD_HHMMSS.db`
- `backups/postgres/trading_ai_YYYYMMDD_HHMMSS.sql.gz` (if `FUTURES_AGENT_DATABASE_URL` is postgres and `pg_dump` available)

Retention: keeps last **14** files per type.

### Recommended schedule

Manual before reboots, or cron/launchd weekly:

```bash
0 4 * * 0 cd ~/polymarket-btc5m-bot && ./scripts/backup-databases.sh >> logs/backup.log 2>&1
```

### Best practice: stop bot before backup (optional, safest)

```bash
./scripts/prod-stop.sh
./scripts/backup-databases.sh
./scripts/prod-start.sh
```

Online backup while running is supported via SQLite backup API (script default).

---

## SQLite verification

```bash
sqlite3 backups/sqlite/trades_LATEST.db "PRAGMA quick_check;"
sqlite3 backups/sqlite/trades_LATEST.db "SELECT COUNT(*) FROM v4_shadow_observations;"
```

---

## SQLite restore (destructive — stop bot first)

```bash
./scripts/prod-stop.sh
cp data/trades.db data/trades.db.before-restore.$(date +%s)
cp backups/sqlite/trades_YYYYMMDD_HHMMSS.db data/trades.db
sqlite3 data/trades.db "PRAGMA quick_check;"
./scripts/prod-start.sh
```

---

## PostgreSQL backup

```bash
pg_dump "$FUTURES_AGENT_DATABASE_URL" | gzip > backups/postgres/trading_ai_manual.sql.gz
```

Requires `FUTURES_AGENT_DATABASE_URL` in environment (from `.env`).

---

## PostgreSQL restore (destructive — test on copy first)

```bash
./scripts/prod-stop.sh   # stop telegram poller at minimum
gunzip -c backups/postgres/trading_ai_YYYYMMDD_HHMMSS.sql.gz | psql "$FUTURES_AGENT_DATABASE_URL"
./scripts/prod-start.sh telegram
python -m bot.research.futures_agent telegram-diagnose
```

**Warning:** `psql` restore to production drops/recreates objects per dump content. Review dump first.

---

## What to back up beyond databases

| Path | Notes |
|------|-------|
| `.env` | **Outside git** — secure copy (1Password, encrypted disk) |
| `data/futures_agent_telegram_offset.json` | Polling cursor |
| `reports/ops_snapshot_*.txt` | Incident snapshots |

---

## Retention policy suggestion

| Tier | Retention |
|------|-----------|
| Hourly (optional) | 24 hours — only during active experiments |
| Daily | 7 days |
| Weekly | 8 weeks |
| Pre-migration manual | indefinite until validated |

Current script: **14 rolling backups** per type (simple default).
