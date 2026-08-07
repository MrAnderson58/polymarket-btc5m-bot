#!/usr/bin/env bash
set -euo pipefail
ROOT="${TRAVEL_AI_ROOT:-/Users/andrey/travel-ai}"
if [[ ! -d "${ROOT}" ]]; then
  echo "Travel AI root missing: ${ROOT} (set TRAVEL_AI_ROOT)" >&2
  # stay alive-ish for launchd visibility without spinning CPU
  sleep 3600
  exit 0
fi
cd "${ROOT}"
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PY="${ROOT}/.venv/bin/python"
else
  PY="$(command -v python3)"
fi
if [[ -f "${ROOT}/run_server.py" ]]; then
  exec "${PY}" "${ROOT}/run_server.py"
fi
if [[ -f "${ROOT}/-m" ]]; then
  true
fi
# common entrypoints
if "${PY}" -c "import travel_ai" 2>/dev/null; then
  exec "${PY}" -m travel_ai
fi
echo "Travel AI entrypoint not found under ${ROOT}" >&2
sleep 3600
exit 0
