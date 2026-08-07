# Server Infrastructure V1 — Mac mini autonomous AI-server

Goal: after any reboot, the AI lab recovers automatically within ~2–3 minutes.

## CLI

```bash
python -m bot.research.market_events ai-server-health
python -m bot.research.market_events ai-server-backup
python -m bot.research.market_events ai-server-watchdog
python -m bot.research.market_events ai-server-git-morning
python -m bot.research.market_events ai-server-install-launchd
python -m bot.research.market_events ai-server-reboot-sim

# equivalent
python -m bot.ops ai-server-health
python -m bot.ops.server_infra_v1 health
```

`ai-server-health` reports PASS/FAIL/WARN for:

SSH · Tailscale · Trading · Travel · Hermes · Dashboard · Disk · RAM · CPU · Database · Queues · Launchd · Backups · Watchdog

## Install launchd (once)

```bash
cd /Users/andrey/polymarket-bot/polymarket-btc5m-bot
./scripts/prod-stop.sh   # avoid duplicate supervisors if still used
python -m bot.ops.server_infra_v1 install-launchd
python -m bot.ops.server_infra_v1 health
```

Templates live under `deploy/macos/`. Agents install into `~/Library/LaunchAgents/`.

## Services (KeepAlive / scheduled)

| Key | launchd label |
|-----|---------------|
| Trading | `com.polymarket.bot-main` |
| Travel | `com.polymarket.travel-ai` (`TRAVEL_AI_ROOT`) |
| Hermes | `com.polymarket.hermes-daily` (daily 06:15) |
| Dashboard | `com.polymarket.dashboard` |
| Learning | `com.polymarket.learning` |
| Event Engine | `com.polymarket.event-engine` |
| News | `com.polymarket.news-intel` |
| Multi-source | `com.polymarket.multi-source` |
| AI Worker | `com.polymarket.ai-worker` |
| Observe | `com.polymarket.observe` |
| Watchdog | `com.polymarket.ai-server-watchdog` (every 5 min) |
| Backup | `com.polymarket.ai-server-backup` (03:15 UTC-ish local) |
| Git morning | `com.polymarket.ai-server-git-morning` (07:00) |
| Boot warmup | `com.polymarket.ai-server-boot-warmup` (RunAtLoad + ~150s delay) |

## SSH

- Enable **System Settings → General → Sharing → Remote Login**
- Ensure `~/.ssh/authorized_keys` has your public key
- Verify: `ssh -o BatchMode=yes localhost echo SSH_OK`

## Tailscale

```bash
tailscale status
# autostart via Tailscale app Login Items / brew services as installed on Mini
```

## Travel AI

Set path if not `~/travel-ai`:

```bash
export TRAVEL_AI_ROOT=/path/to/travel-ai
# or disable check:
export AI_SERVER_TRAVEL_ENABLED=0
```

## Backups

Nightly archive under `backups/ai_server_v1/ai_server_YYYYMMDD_HHMMSS.tar.gz` containing SQLite copies, key JSON, reports, and git diff bundle. Retention: **30 days**.

## Watchdog

Every 5 minutes: if a KeepAlive agent is not loaded, `launchctl kickstart` / bootstrap.

## Git morning

Writes `GIT_MORNING_REPORT.md` with `git status`, `git fetch`, HEAD, branch. **No pull / no push.**

## Hermes

After research package build (`daily-research-package` / post `build-research-lake`), run:

```bash
python -m bot.research.market_events daily-hermes-report
```

Hermes reads only `RESEARCH_PACKAGE.json` (Autonomous V2). Prefer offline/cheap path when Claude is unavailable.

## Workspace

Open `AI-LAB.code-workspace` (Trading + Travel + Hermes Docs).

## Reboot simulation

```bash
python -m bot.ops.server_infra_v1 reboot-sim
```

Does **not** reboot the machine; exercises reconnect probes, watchdog dry-run, DB, backup.

## Finish gate

Ship only when `ai-server-health` overall PASS (launchd installed, SSH key auth, Tailscale up, DB OK, watchdog loaded, recent backup).
