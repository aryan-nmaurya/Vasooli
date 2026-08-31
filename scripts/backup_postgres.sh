#!/usr/bin/env bash
set -euo pipefail

# Usage: DATABASE_URL=postgresql://... ./scripts/backup_postgres.sh [output.dump]
output_path="${1:-vasooli-$(date -u +%Y%m%dT%H%M%SZ).dump}"
: "${DATABASE_URL:?DATABASE_URL must point at the Docker/Postgres database}"

pg_dump --format=custom --no-owner --no-acl --file "$output_path" "$DATABASE_URL"
pg_restore --list "$output_path" >/dev/null
echo "verified backup: $output_path"
