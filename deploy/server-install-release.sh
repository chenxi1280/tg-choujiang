#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BASE_DIR="${BASE_DIR:-/data/tg-lottery}"
RELEASE_ID="${RELEASE_ID:-$(basename "$RELEASE_DIR")}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-dir) BASE_DIR="$2"; shift 2 ;;
    --release-dir) RELEASE_DIR="$2"; shift 2 ;;
    --release-id) RELEASE_ID="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

SHARED_DIR="${BASE_DIR}/shared"
CURRENT_LINK="${BASE_DIR}/current"
RELEASES_DIR="${BASE_DIR}/releases"
INCOMING_DIR="${BASE_DIR}/incoming"
BACKUP_DIR="${BASE_DIR}/backups"

command -v docker >/dev/null 2>&1 || { echo "Missing docker" >&2; exit 1; }
mkdir -p "$RELEASES_DIR" "$SHARED_DIR" "$INCOMING_DIR" "$BACKUP_DIR" "${BASE_DIR}/logs"

if [[ ! -f "${SHARED_DIR}/.env" ]]; then
  if [[ -f "${RELEASE_DIR}/.env.docker.example" ]]; then
    cp "${RELEASE_DIR}/.env.docker.example" "${SHARED_DIR}/.env"
    echo "Created ${SHARED_DIR}/.env from example. Fill secrets and rerun." >&2
    exit 1
  fi
  echo "Missing ${SHARED_DIR}/.env" >&2
  exit 1
fi

APP_DIR="${RELEASE_DIR}" BASE_DIR="${BASE_DIR}" SHARED_DIR="${SHARED_DIR}" ENV_FILE="${SHARED_DIR}/.env" \
  bash "${RELEASE_DIR}/deploy/compose-up.sh"

ln -sfn "$RELEASE_DIR" "${CURRENT_LINK}.tmp"
mv -Tf "${CURRENT_LINK}.tmp" "$CURRENT_LINK"

mapfile -t release_paths < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d | sort)
if (( ${#release_paths[@]} > KEEP_RELEASES )); then
  remove_count=$(( ${#release_paths[@]} - KEEP_RELEASES ))
  current_target="$(readlink -f "$CURRENT_LINK" || true)"
  for ((i = 0; i < remove_count; i++)); do
    [[ "${release_paths[$i]}" == "$current_target" ]] || rm -rf "${release_paths[$i]}"
  done
fi

echo "Release ${RELEASE_ID} is live"
echo "current -> $(readlink -f "$CURRENT_LINK")"

