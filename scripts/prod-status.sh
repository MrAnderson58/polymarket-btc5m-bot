#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/prod-common.sh
source "$(cd "$(dirname "$0")" && pwd)/prod-common.sh"
exec "${PYTHON}" -m bot.ops prod-control status "$@"
