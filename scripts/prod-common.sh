#!/usr/bin/env bash
# Shared production script settings — Mac Mini polymarket-btc5m-bot
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "ERROR: project venv not found at ${PYTHON}" >&2
  exit 1
fi

cd "${REPO_ROOT}"

# Load .env for child processes if present (never print contents)
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi
