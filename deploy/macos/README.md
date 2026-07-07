# macOS launchd — templates (not auto-installed)

Paths assume Mac Mini user `andrey` and repo at:

`/Users/andrey/polymarket-bot/polymarket-btc5m-bot`

Adjust `WorkingDirectory`, wrapper scripts, and log paths if your layout differs.

## Files

| File | Service |
|------|---------|
| `com.polymarket.bot-main.plist` | Main bot |
| `com.polymarket.futures-agent-telegram.plist` | Telegram poller |
| `run-bot-main.sh` | Wrapper: sources `.env`, execs `python -m bot.main` |
| `run-futures-telegram.sh` | Wrapper: sources `.env`, execs `telegram-poll` |

**Secrets are never embedded in plists.** Wrappers source `~/polymarket-btc5m-bot/.env` at runtime.

## Avoid duplicates

**Do not** run both launchd and `scripts/prod-start.sh` for the same service.

Before enabling launchd:

```bash
./scripts/prod-stop.sh
./scripts/prod-status.sh   # must show STOPPED for both
```

## Install (manual)

```bash
cd ~/polymarket-btc5m-bot
mkdir -p logs ~/Library/LaunchAgents

cp deploy/macos/com.polymarket.bot-main.plist ~/Library/LaunchAgents/
cp deploy/macos/com.polymarket.futures-agent-telegram.plist ~/Library/LaunchAgents/

launchctl bootout gui/$(id -u)/com.polymarket.bot-main 2>/dev/null || true
launchctl bootout gui/$(id -u)/com.polymarket.futures-agent-telegram 2>/dev/null || true

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.polymarket.bot-main.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.polymarket.futures-agent-telegram.plist

launchctl enable gui/$(id -u)/com.polymarket.bot-main
launchctl enable gui/$(id -u)/com.polymarket.futures-agent-telegram
```

## Status

```bash
launchctl print gui/$(id -u)/com.polymarket.bot-main
launchctl print gui/$(id -u)/com.polymarket.futures-agent-telegram
./scripts/prod-status.sh
python -m bot.ops healthcheck
```

## Restart

```bash
launchctl kickstart -k gui/$(id -u)/com.polymarket.bot-main
launchctl kickstart -k gui/$(id -u)/com.polymarket.futures-agent-telegram
```

Or use recovery scripts (if not using launchd):

```bash
./scripts/prod-restart.sh
```

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.polymarket.bot-main
launchctl bootout gui/$(id -u)/com.polymarket.futures-agent-telegram
rm -f ~/Library/LaunchAgents/com.polymarket.bot-main.plist
rm -f ~/Library/LaunchAgents/com.polymarket.futures-agent-telegram.plist
```

## KeepAlive policy

- **bot-main:** `KeepAlive=true` — respawn on crash (watch for restart loops; check logs)
- **futures-agent-telegram:** `KeepAlive` on unsuccessful exit only — clean `KeyboardInterrupt`/SIGTERM won't respawn immediately

## PostgreSQL

launchd does not start PostgreSQL. Ensure Postgres is running before bot-main / telegram poller:

```bash
brew services start postgresql@16   # example; adjust to your install
pg_isready
```

## Logs

- `logs/launchd-bot-main.log`
- `logs/launchd-bot-main.err.log`
- `logs/launchd-futures-agent-telegram.log`
- `logs/launchd-futures-agent-telegram.err.log`
