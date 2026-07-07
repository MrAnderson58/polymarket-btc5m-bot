#!/usr/bin/env bash
# Safe database backups — SQLite online backup API + PostgreSQL pg_dump.
set -euo pipefail
# shellcheck source=scripts/prod-common.sh
source "$(cd "$(dirname "$0")" && pwd)/prod-common.sh"

STAMP="$(date -u +"%Y%m%d_%H%M%S")"
BACKUP_ROOT="${REPO_ROOT}/backups"
SQLITE_BACKUP_DIR="${BACKUP_ROOT}/sqlite"
PG_BACKUP_DIR="${BACKUP_ROOT}/postgres"
mkdir -p "${SQLITE_BACKUP_DIR}" "${PG_BACKUP_DIR}"

# Resolve SQLite path from env or default
SQLITE_SRC="${DATABASE_PATH:-${REPO_ROOT}/data/trades.db}"
SQLITE_DEST="${SQLITE_BACKUP_DIR}/trades_${STAMP}.db"

echo "SQLite backup: ${SQLITE_SRC} -> ${SQLITE_DEST}"
export SQLITE_SRC SQLITE_DEST
"${PYTHON}" - <<'PY'
import os
import sqlite3
from pathlib import Path

src = Path(os.environ["SQLITE_SRC"])
dest = Path(os.environ["SQLITE_DEST"])
if not src.is_file():
    raise SystemExit(f"SQLite source missing: {src}")
dest.parent.mkdir(parents=True, exist_ok=True)
src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
try:
    dest_conn = sqlite3.connect(dest)
    try:
        src_conn.backup(dest_conn)
        dest_conn.commit()
    finally:
        dest_conn.close()
finally:
    src_conn.close()
print("SQLite backup OK")
PY

# Verify backup
sqlite3 "${SQLITE_DEST}" "PRAGMA quick_check;" | grep -q '^ok$'
echo "SQLite quick_check: ok"

# PostgreSQL trading_ai (optional — requires pg_dump + FUTURES_AGENT_DATABASE_URL)
if [[ -n "${FUTURES_AGENT_DATABASE_URL:-}" ]] && [[ "${FUTURES_AGENT_DATABASE_URL}" == postgres* ]]; then
  PG_DEST="${PG_BACKUP_DIR}/trading_ai_${STAMP}.sql.gz"
  echo "PostgreSQL backup via pg_dump -> ${PG_DEST}"
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "WARNING: pg_dump not found; skipping PostgreSQL backup" >&2
  else
    pg_dump "${FUTURES_AGENT_DATABASE_URL}" | gzip -c > "${PG_DEST}"
    echo "PostgreSQL backup OK"
  fi
else
  echo "FUTURES_AGENT_DATABASE_URL not set to postgres — skipping pg_dump"
fi

# Retention: keep last 14 daily-equivalent backups (by count)
keep_last() {
  local dir="$1" pattern="$2" keep="$3"
  local files
  files=$(ls -1t "${dir}"/${pattern} 2>/dev/null | tail -n +$((keep + 1)) || true)
  if [[ -n "${files}" ]]; then
    echo "${files}" | xargs rm -f
  fi
}

keep_last "${SQLITE_BACKUP_DIR}" "trades_*.db" 14
keep_last "${PG_BACKUP_DIR}" "trading_ai_*.sql.gz" 14

echo "Backup complete: ${STAMP}"
