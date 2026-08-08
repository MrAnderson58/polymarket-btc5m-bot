#!/usr/bin/env bash
set -euo pipefail
REPO="/Users/andrey/polymarket-bot/polymarket-btc5m-bot"
cd "${REPO}"
if [[ -f "${REPO}/.env" ]]; then set -a; source "${REPO}/.env"; set +a; fi
PY="${REPO}/.venv/bin/python"
[[ -x "${PY}" ]] || PY="$(command -v python3)"
exec "${PY}" -m bot.research.market_events hermes-weekly
