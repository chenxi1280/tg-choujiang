#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/docker-env.sh"

ensure_runtime_env

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-app-infra-postgres}"
POSTGRES_DATABASE="${POSTGRES_DATABASE:-tg_lottery}"
POSTGRES_BACKUP_KEEP="${POSTGRES_BACKUP_KEEP:-14}"
BACKUP_DIR="${BASE_DIR}/backups/postgres"
BACKUP_NAME="${RELEASE_ID:-manual}_$(date -u '+%Y%m%dT%H%M%SZ').dump"

mkdir -p "$BACKUP_DIR"
temporary_path="$(mktemp "${BACKUP_DIR}/.${BACKUP_NAME}.XXXXXX")"
trap 'rm -f "$temporary_path"' EXIT

docker_cmd exec "$POSTGRES_CONTAINER" \
  pg_dump -U postgres -d "$POSTGRES_DATABASE" --format=custom --no-owner --no-acl \
  >"$temporary_path"
test -s "$temporary_path"
chmod 600 "$temporary_path"
mv "$temporary_path" "${BACKUP_DIR}/${BACKUP_NAME}"

mapfile -t backups < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.dump' | sort)
if (( ${#backups[@]} > POSTGRES_BACKUP_KEEP )); then
  remove_count=$(( ${#backups[@]} - POSTGRES_BACKUP_KEEP ))
  for ((index = 0; index < remove_count; index++)); do
    rm -f "${backups[$index]}"
  done
fi

echo "Database backup created: ${BACKUP_DIR}/${BACKUP_NAME}"
