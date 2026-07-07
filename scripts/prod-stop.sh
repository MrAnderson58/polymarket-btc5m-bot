#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/prod-common.sh
source "$(cd "$(dirname "$0")" && pwd)/prod-common.sh"

COMPONENT="${1:-all}"
if [[ "${COMPONENT}" != "all" && "${COMPONENT}" != "main" && "${COMPONENT}" != "telegram" ]]; then
  echo "Usage: $0 [all|main|telegram]" >&2
  exit 1
fi

exec "${PYTHON}" -m bot.ops prod-control stop --component "${COMPONENT}"
