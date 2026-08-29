#!/usr/bin/env bash
# Restore a dump into a uniquely named throwaway database and validate core tables.
# Never points pg_restore/psql at the live application database.
set -euo pipefail
umask 077

cd "$(dirname "$0")"
mkdir -p backups
STAMP=$(date +%Y%m%d%H%M%S)
DRILL_DB="vasooli_restore_drill_${STAMP}"
if [[ ! "${DRILL_DB}" =~ ^vasooli_restore_drill_[0-9]{14}$ ]]; then
  echo "unsafe restore database name" >&2
  exit 1
fi

BACKUP_FILE=${1:-}
TEMP_DIR=""
if [[ -z "${BACKUP_FILE}" ]]; then
  BACKUP_FILE=$(find backups -type f -name 'vasooli-*.sql.gz' -print | sort | tail -n 1)
fi
if [[ -z "${BACKUP_FILE}" && -f .env ]]; then
  BACKUP_S3_URI=$(sed -n 's/^BACKUP_S3_URI=//p' .env | tail -n 1)
  if [[ -n "${BACKUP_S3_URI}" ]]; then
    TEMP_DIR=$(mktemp -d)
    LATEST_KEY=$(aws s3 ls "${BACKUP_S3_URI%/}/" | awk '{print $4}' | sort | tail -n 1)
    [[ -n "${LATEST_KEY}" ]] || { echo "no S3 backup found" >&2; exit 1; }
    BACKUP_FILE="${TEMP_DIR}/${LATEST_KEY}"
    aws s3 cp "${BACKUP_S3_URI%/}/${LATEST_KEY}" "${BACKUP_FILE}"
  fi
fi
[[ -f "${BACKUP_FILE}" ]] || { echo "backup not found: ${BACKUP_FILE:-none}" >&2; exit 1; }
gzip -t "${BACKUP_FILE}"

DATABASE_URL="${DATABASE_URL:-}"
if [[ -z "${DATABASE_URL}" && -f .env ]]; then
  DATABASE_URL=$(sed -n 's/^DATABASE_URL=//p' .env | tail -n 1)
fi

cleanup() {
  if [[ -n "${DATABASE_URL}" ]]; then
    ADMIN_URL=$(printf '%s' "${DATABASE_URL}" | sed -E 's@/[^/?]+([?].*)?$@/postgres\1@')
    docker run --rm postgres:17 psql "${ADMIN_URL}" -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS \"${DRILL_DB}\" WITH (FORCE);" >/dev/null || true
  else
    docker compose -f docker-compose.prod.yml exec -T db \
      dropdb -U "${POSTGRES_USER:-vasooli}" --if-exists --force "${DRILL_DB}" >/dev/null || true
  fi
  if [[ -n "${TEMP_DIR}" ]]; then
    rm -f -- "${BACKUP_FILE}"
    rmdir "${TEMP_DIR}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ -n "${DATABASE_URL}" ]]; then
  ADMIN_URL=$(printf '%s' "${DATABASE_URL}" | sed -E 's@/[^/?]+([?].*)?$@/postgres\1@')
  DRILL_URL=$(
    printf '%s' "${DATABASE_URL}" \
      | sed -E 's@/[^/?]+([?].*)?$@/'"${DRILL_DB}"'\1@'
  )
  docker run --rm postgres:17 psql "${ADMIN_URL}" -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE \"${DRILL_DB}\";"
  gzip -dc "${BACKUP_FILE}" | docker run --rm -i postgres:17 \
    psql "${DRILL_URL}" -v ON_ERROR_STOP=1 >/dev/null
  COUNTS=$(docker run --rm postgres:17 psql "${DRILL_URL}" -At -v ON_ERROR_STOP=1 \
    -c "SELECT (SELECT count(*) FROM invoices) || ':' || (SELECT count(*) FROM audit_logs);")
else
  docker compose -f docker-compose.prod.yml exec -T db \
    createdb -U "${POSTGRES_USER:-vasooli}" "${DRILL_DB}"
  gzip -dc "${BACKUP_FILE}" | docker compose -f docker-compose.prod.yml exec -T db \
    psql -U "${POSTGRES_USER:-vasooli}" -d "${DRILL_DB}" -v ON_ERROR_STOP=1 >/dev/null
  COUNTS=$(docker compose -f docker-compose.prod.yml exec -T db \
    psql -U "${POSTGRES_USER:-vasooli}" -d "${DRILL_DB}" -At -v ON_ERROR_STOP=1 \
    -c "SELECT (SELECT count(*) FROM invoices) || ':' || (SELECT count(*) FROM audit_logs);")
fi

echo "restore drill ok: ${BACKUP_FILE}; invoices:audit_logs=${COUNTS}"
