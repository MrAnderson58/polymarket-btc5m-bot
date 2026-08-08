#!/usr/bin/env bash
# launchd-safe paper/research-only bot.main — never enable live via this path
set -euo pipefail
REPO="/Users/andrey/polymarket-bot/polymarket-btc5m-bot"
cd "${REPO}"
if [[ -f "${REPO}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO}/.env"
  set +a
fi
# Paper-only guard (research recovery path)
export TRADING_MODE=paper
export LIVE_ENABLED=false
export LIVE_EXIT_ENABLED=false
# Refuse accidental live
if [[ "${TRADING_MODE}" == "live" ]]; then
  echo "REFUSE: live trading blocked in paper launchd wrapper" >&2
  exit 78
fi
PY="${REPO}/.venv/bin/python"
if [[ ! -x "${PY}" ]]; then
  echo "ERROR: missing venv python ${PY}" >&2
  exit 1
fi
# Single-instance soft check
if pgrep -f "[P]ython -m bot.main" >/dev/null 2>&1 || pgrep -f "[p]ython -m bot.main" >/dev/null 2>&1; then
  # If already running under this launchd, exec would replace; allow launchd KeepAlive path.
  # Only refuse if a *foreign* instance exists without our XPC service — keep simple: log and continue under launchd.
  echo "WARN: bot.main already present; launchd will manage single instance" >&2
fi
exec "${PY}" -m bot.main
