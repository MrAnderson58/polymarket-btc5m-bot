#!/usr/bin/env bash
# launchd-safe wrapper — loads project .env then runs market_events command
set -euo pipefail
REPO="/Users/andrey/polymarket-bot/polymarket-btc5m-bot"
cd "${REPO}"
if [[ -f "${REPO}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO}/.env"
  set +a
fi
PY="${REPO}/.venv/bin/python"
if [[ ! -x "${PY}" ]]; then
  PY="$(command -v python3)"
fi
exec "${PY}" -m bot.research.market_events event-engine-run
