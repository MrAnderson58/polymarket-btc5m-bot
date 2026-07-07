#!/usr/bin/env bash
# launchd-safe wrapper — loads project .env then execs futures telegram-poll
set -euo pipefail
REPO="/Users/andrey/polymarket-bot/polymarket-btc5m-bot"
cd "${REPO}"
if [[ -f "${REPO}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO}/.env"
  set +a
fi
exec "${REPO}/.venv/bin/python" -m bot.research.futures_agent telegram-poll
