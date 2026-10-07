#!/bin/bash
# pg_dump → gzip，保留 7 天。需要能連到 DATABASE_URL 且有 pg_dump 的環境。
# 這支是簡易備份；正式環境請自行用 pg_dump -Fc 搭配排程與還原演練。
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DATABASE_URL="${DATABASE_URL:?DATABASE_URL environment variable is required}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="cryptomind_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

pg_dump "${DATABASE_URL}" | gzip > "${BACKUP_DIR}/${FILENAME}"

find "${BACKUP_DIR}" -name "*.sql.gz" -mtime +7 -delete

echo "Backup created: ${BACKUP_DIR}/${FILENAME}"
