#!/usr/bin/env bash

set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BASE_DIR="${BASE_DIR:-/data/tg-lottery}"
SHARED_DIR="${SHARED_DIR:-${BASE_DIR}/shared}"
export ENV_FILE="${ENV_FILE:-${SHARED_DIR}/.env}"
IMAGE_ENV_FILE="${IMAGE_ENV_FILE:-${APP_DIR}/.image.env}"
DOCKER_COMMAND=()

require_command() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }
}

ensure_runtime_env() {
  require_command docker
  resolve_docker_command
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing env file: ${ENV_FILE}" >&2
    exit 1
  fi

  local action_ghcr_username="${GHCR_USERNAME:-}"
  local action_ghcr_token="${GHCR_TOKEN:-}"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  if [[ -f "$IMAGE_ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$IMAGE_ENV_FILE"
  fi
  set +a
  [[ -n "$action_ghcr_username" ]] && export GHCR_USERNAME="$action_ghcr_username"
  [[ -n "$action_ghcr_token" ]] && export GHCR_TOKEN="$action_ghcr_token"

  local required=(BOT_TOKEN DATABASE_URL APP_ENCRYPTION_KEY TG_LOTTERY_APP_IMAGE)
  local missing=()
  local key
  for key in "${required[@]}"; do
    [[ -n "${!key:-}" ]] || missing+=("$key")
  done
  if (( ${#missing[@]} > 0 )); then
    echo "Missing runtime env vars: ${missing[*]}" >&2
    exit 1
  fi
}

resolve_docker_command() {
  if docker info >/dev/null 2>&1; then
    DOCKER_COMMAND=(docker)
    return
  fi
  if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    DOCKER_COMMAND=(sudo -n docker)
    return
  fi
  echo "Current user cannot access Docker directly or through passwordless sudo" >&2
  exit 1
}

docker_cmd() {
  "${DOCKER_COMMAND[@]}" "$@"
}

compose() {
  local env_args=(--env-file "$ENV_FILE")
  if [[ -f "$IMAGE_ENV_FILE" ]]; then
    env_args+=(--env-file "$IMAGE_ENV_FILE")
  fi
  (cd "$APP_DIR" && docker_cmd compose "${env_args[@]}" -f "$APP_DIR/docker-compose.yml" "$@")
}
