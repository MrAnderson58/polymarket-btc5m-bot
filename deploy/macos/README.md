# macOS launchd — Server Infrastructure V1

Primary docs: [`docs/operations/SERVER_INFRASTRUCTURE_V1.md`](../../docs/operations/SERVER_INFRASTRUCTURE_V1.md)

```bash
python -m bot.ops.server_infra_v1 install-launchd
python -m bot.research.market_events ai-server-health
```

Paths assume Mac Mini user `andrey` and repo at:

`/Users/andrey/polymarket-bot/polymarket-btc5m-bot`

**Secrets are never embedded in plists.** Wrappers source `.env` at runtime.

## Avoid duplicates

**Do not** run both launchd KeepAlive agents and `scripts/prod-start.sh` for the same service.

## SSH (manual once)

1. Ensure `~/.ssh/authorized_keys` contains your public key
2. Enable **System Settings → General → Sharing → Remote Login**
3. Verify: `ssh -o BatchMode=yes localhost echo SSH_OK`

## Status

```bash
python -m bot.research.market_events ai-server-health
launchctl print gui/$(id -u)/com.polymarket.ai-server-watchdog
```
