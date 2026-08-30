#!/usr/bin/env bash
set -euo pipefail

# Restore only into an explicitly supplied database. Never defaults to the workspace
# or production database, and never drops objects implicitly.
backup_path="${1:?usage: DATABASE_URL=... ./scripts/restore_postgres.sh backup.dump}"
: "${DATABASE_URL:?DATABASE_URL must point at an isolated restore target}"
test -f "$backup_path"
pg_restore --no-owner --no-acl --clean --if-exists --exit-on-error --dbname "$DATABASE_URL" "$backup_path"
echo "restored and verified: $backup_path"
