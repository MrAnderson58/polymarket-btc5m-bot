# Reboot Runbook — Mac Mini

Target recovery time: **2–3 minutes** after macOS is back online when Server Infrastructure V1 launchd agents are installed.

See also: [`SERVER_INFRASTRUCTURE_V1.md`](SERVER_INFRASTRUCTURE_V1.md)

```bash
python -m bot.research.market_events ai-server-health
python -m bot.research.market_events ai-server-reboot-sim
```

Repository: `/Users/andrey/polymarket-bot/polymarket-btc5m-bot`  
Branch: `develop-terminal`

---

## BEFORE REBOOT

### 1. Git status

```bash
cd ~/polymarket-btc5m-bot
git status
```

Record any uncommitted changes. Prefer clean tree or note dirty files.

### 2. Git log

```bash
git log -3 --oneline
```

### 3. Identify running processes

```bash
./scripts/prod-status.sh
# or
python -m bot.ops healthcheck
```

### 4. Stop processes gracefully

```bash
./scripts/prod-stop.sh
```

Wait until status shows STOPPED. Scripts send SIGTERM first (30s), then SIGKILL if needed.

### 5. Verify DB integrity / readability

```bash
sqlite3 data/trades.db "PRAGMA quick_check;"
pg_isready   # if using PostgreSQL for futures_agent
```

### 6. Optional backup

```bash
./scripts/backup-databases.sh
```

See `docs/operations/BACKUP_RESTORE.md`.

### 7. Record commit SHA

```bash
git rev-parse HEAD > /tmp/polymarket-pre-reboot-sha.txt
cat /tmp/polymarket-pre-reboot-sha.txt
```

### 8. Reboot

```bash
sudo shutdown -r now
```

---

## AFTER REBOOT

### 1. Open repo

```bash
cd ~/polymarket-btc5m-bot
```

### 2. Activate venv

```bash
source .venv/bin/activate
```

### 3. Git status

```bash
git status
```

### 4. Git log (confirm same commit unless you pulled)

```bash
git log -1 --oneline
```

### 5. Verify `.env` exists

```bash
test -f .env && echo ".env present" || echo "MISSING .env"
```

Never print `.env` contents to shared logs.

### 6. Healthcheck (expect CRITICAL until services started)

```bash
python -m bot.ops healthcheck
```

### 7. Start PostgreSQL if required

```bash
brew services start postgresql@16   # adjust version
pg_isready
psql -d trading_ai -c "SELECT 1"
```

### 8. Start main bot

```bash
./scripts/prod-start.sh main
# OR if using launchd: launchctl kickstart gui/$(id -u)/com.polymarket.bot-main
```

### 9. Start Telegram poller

```bash
./scripts/prod-start.sh telegram
```

### 10. Verify no duplicate processes

```bash
./scripts/prod-status.sh
```

Must show **exactly 1** instance each. Exit code 2 = duplicate warning.

### 11. Verify logs

```bash
tail -n 30 logs/bot-main.log
tail -n 30 logs/futures-agent-telegram.log
```

### 12. Wait one full BTC 5m market (~5 minutes)

Watch for new window slug in `logs/bot-main.log`.

### 13. Run V4 density audit

```bash
python -m bot.collector_diagnostics v4-density --last-markets 5
```

### 14. Verify collector metrics

On **completed** markets (not the in-progress window):

- `obs/market` **≥ 60** (target ~89–113)
- `median gap` **≤ 5 sec** (target ~3 sec)

### 15. Futures agent diagnose

```bash
python -m bot.research.futures_agent telegram-diagnose
```

Expect: token configured, webhook inactive, polling conflict risk low, DB reachable.

### 16. Final decision

```bash
python -m bot.ops healthcheck
python -m bot.ops snapshot
```

| Status | Meaning |
|--------|---------|
| **HEALTHY** | All processes up, DB OK, collector within thresholds |
| **DEGRADED** | Running but warnings (stale obs, dirty git, disk low) |
| **CRITICAL** | Missing process, duplicate, DB failure, density collapse |

### 17. If DEGRADED/CRITICAL

See `docs/operations/BLACKOUT_RECOVERY.md` and re-check:

- duplicate processes
- stale `data/futures_agent_telegram_poll.lock`
- `ENABLE_V4_SHADOW=true`
- `V4_POLL_INTERVAL_SEC=1`, `MTF_POLL_INTERVAL_SEC=60`

**Do not** revert V4 collector decoupling architecture.

---

## Pre-reboot checklist (printable)

- [ ] `git status` / `git log` recorded
- [ ] `./scripts/prod-stop.sh` — both STOPPED
- [ ] `sqlite3 data/trades.db "PRAGMA quick_check;"` → ok
- [ ] Optional `./scripts/backup-databases.sh`
- [ ] Commit SHA saved
- [ ] Reboot

## Post-reboot checklist (printable)

- [ ] `cd ~/polymarket-btc5m-bot && source .venv/bin/activate`
- [ ] `.env` present
- [ ] PostgreSQL up (if used)
- [ ] `./scripts/prod-start.sh` — no duplicates
- [ ] Logs streaming new lines
- [ ] Wait 1× 5m market
- [ ] `v4-density` — obs≥60, gap≤5s
- [ ] `telegram-diagnose` OK
- [ ] `python -m bot.ops healthcheck` → HEALTHY or documented DEGRADED
