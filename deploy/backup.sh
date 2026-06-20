#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/owo-boss-helper}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/owo-boss-helper}"
KEEP_DAYS="${KEEP_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="${BACKUP_DIR}/work-${TIMESTAMP}"
ARCHIVE="${BACKUP_DIR}/owo-boss-helper-${TIMESTAMP}.tar.gz"

mkdir -p "${WORK_DIR}"
chmod 700 "${BACKUP_DIR}" "${WORK_DIR}"

for database in team_templates.db boss_tickets.db bot_stats.db; do
    if [[ -f "${APP_DIR}/${database}" ]]; then
        sqlite3 "${APP_DIR}/${database}" ".backup '${WORK_DIR}/${database}'"
    fi
done

for file in boss_cooldown_config.json; do
    if [[ -f "${APP_DIR}/${file}" ]]; then
        cp --preserve=mode,timestamps "${APP_DIR}/${file}" "${WORK_DIR}/${file}"
    fi
done

tar -C "${WORK_DIR}" -czf "${ARCHIVE}" .
chmod 600 "${ARCHIVE}"
rm -rf "${WORK_DIR}"
find "${BACKUP_DIR}" -type f -name 'owo-boss-helper-*.tar.gz' -mtime "+${KEEP_DAYS}" -delete

echo "Created ${ARCHIVE}"
