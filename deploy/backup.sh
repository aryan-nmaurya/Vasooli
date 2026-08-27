#!/usr/bin/env bash
# Nightly Postgres dump. Add to the host's crontab:
#   0 3 * * * /home/ubuntu/vasooli/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
#
# Keeps 14 days locally. That is enough for the failure this actually guards against —
# a bad migration or a wrong DELETE — and not a substitute for off-box storage. If the
# instance dies, so do these; push them to S3 once the box matters.
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)

docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U "${POSTGRES_USER:-vasooli}" -d "${POSTGRES_DB:-vasooli}" \
  | gzip > "backups/vasooli-${STAMP}.sql.gz"

find backups -name 'vasooli-*.sql.gz' -mtime +14 -delete
echo "backup ok: backups/vasooli-${STAMP}.sql.gz ($(du -h "backups/vasooli-${STAMP}.sql.gz" | cut -f1))"
