#!/usr/bin/env bash
# Nightly Postgres dump. Add to the host's crontab:
#   0 3 * * * /home/ubuntu/vasooli/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
#
# Keeps 14 days locally for fast rollback and uploads off-host when configured. In the
# recommended production configuration REQUIRE_OFFSITE_BACKUP=true turns a missing S3
# destination into a failed job, which the external dead-man monitor can alert on.
set -euo pipefail
umask 077

cd "$(dirname "$0")"
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)

DATABASE_URL="${DATABASE_URL:-}"
if [[ -z "${DATABASE_URL}" && -f .env ]]; then
  DATABASE_URL=$(sed -n 's/^DATABASE_URL=//p' .env | tail -n 1)
fi
if [[ -n "${DATABASE_URL}" ]]; then
  docker run --rm postgres:17 pg_dump "${DATABASE_URL}" \
    | gzip > "backups/vasooli-${STAMP}.sql.gz"
else
  docker compose -f docker-compose.prod.yml exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    | gzip > "backups/vasooli-${STAMP}.sql.gz"
fi

find backups -name 'vasooli-*.sql.gz' -mtime +14 -delete
# Compose reads deploy/.env itself, but this host script does not. Read only this
# single non-secret destination instead of sourcing a Docker env file as shell code.
if [[ -z "${BACKUP_S3_URI:-}" && -f .env ]]; then
  BACKUP_S3_URI=$(sed -n 's/^BACKUP_S3_URI=//p' .env | tail -n 1)
fi
if [[ -z "${REQUIRE_OFFSITE_BACKUP:-}" && -f .env ]]; then
  REQUIRE_OFFSITE_BACKUP=$(sed -n 's/^REQUIRE_OFFSITE_BACKUP=//p' .env | tail -n 1)
fi
if [[ -z "${BACKUP_S3_KMS_KEY_ID:-}" && -f .env ]]; then
  BACKUP_S3_KMS_KEY_ID=$(sed -n 's/^BACKUP_S3_KMS_KEY_ID=//p' .env | tail -n 1)
fi
if [[ -n "${BACKUP_S3_URI:-}" ]]; then
  if [[ "${BACKUP_S3_URI}" != s3://* ]]; then
    echo "BACKUP_S3_URI must start with s3://" >&2
    exit 1
  fi
  # Encrypt at rest in the off-host bucket. Production should set a customer-managed
  # KMS key; AES256 remains a safer fallback than an unencrypted upload.
  if [[ -n "${BACKUP_S3_KMS_KEY_ID:-}" ]]; then
    aws s3 cp "backups/vasooli-${STAMP}.sql.gz" "${BACKUP_S3_URI%/}/vasooli-${STAMP}.sql.gz" \
      --sse aws:kms --sse-kms-key-id "${BACKUP_S3_KMS_KEY_ID}"
  else
    aws s3 cp "backups/vasooli-${STAMP}.sql.gz" "${BACKUP_S3_URI%/}/vasooli-${STAMP}.sql.gz" \
      --sse AES256
  fi
else
  if [[ "${REQUIRE_OFFSITE_BACKUP:-false}" == "true" ]]; then
    echo "BACKUP_S3_URI is required when REQUIRE_OFFSITE_BACKUP=true" >&2
    exit 1
  fi
  echo "warning: BACKUP_S3_URI is unset; this backup exists only on the EC2 host" >&2
fi
sha256sum "backups/vasooli-${STAMP}.sql.gz" > "backups/vasooli-${STAMP}.sha256"
echo "backup ok: backups/vasooli-${STAMP}.sql.gz ($(du -h "backups/vasooli-${STAMP}.sql.gz" | cut -f1))"
