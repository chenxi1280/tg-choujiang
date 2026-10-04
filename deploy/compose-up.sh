#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/docker-env.sh"

ensure_runtime_env

if [[ "$TG_LOTTERY_APP_IMAGE" == ghcr.io/* ]]; then
  if [[ -z "${GHCR_USERNAME:-}" || -z "${GHCR_TOKEN:-}" ]]; then
    echo "Pulling GHCR image requires GHCR_USERNAME and GHCR_TOKEN" >&2
    exit 1
  fi
  printf '%s\n' "$GHCR_TOKEN" | docker_cmd login ghcr.io -u "$GHCR_USERNAME" --password-stdin >/dev/null
fi

export RELEASE_ID="${RELEASE_ID:-unknown}"
export RELEASE_SHA="${RELEASE_SHA:-${RELEASE_ID##*_}}"

echo "==> Pulling application image"
compose pull app worker migrate

# Inspect executable image capability before migrations or runtime replacement.
rule_capabilities="$(compose run --rm --no-deps -T app python -m tg_lottery.rule_capabilities --describe)"
compose run --rm --no-deps -T app python -m tg_lottery.rule_capabilities --check "$rule_capabilities"

echo "==> Backing up application database"
APP_DIR="$APP_DIR" BASE_DIR="$BASE_DIR" ENV_FILE="$ENV_FILE" IMAGE_ENV_FILE="$IMAGE_ENV_FILE" \
  bash "$SCRIPT_DIR/backup-database.sh"

echo "==> Running database migrations"
compose --profile tools run --rm --no-deps migrate

echo "==> Starting app and worker"
compose up -d --no-build --remove-orphans app worker

timeout_seconds="${TG_LOTTERY_HEALTH_TIMEOUT_SECONDS:-600}"
started_at="$(date +%s)"
while true; do
  app_state="$(docker_cmd inspect tg-lottery-app --format '{{.State.Status}}' 2>/dev/null || true)"
  worker_state="$(docker_cmd inspect tg-lottery-worker --format '{{.State.Status}}' 2>/dev/null || true)"
  app_health="$(docker_cmd inspect tg-lottery-app --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"
  worker_health="$(docker_cmd inspect tg-lottery-worker --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"
  if [[ "$app_health" == "healthy" && "$worker_health" == "healthy" ]]; then
    break
  fi
  if [[ "$app_state" =~ ^(dead|exited|restarting)$ ]] ||
    [[ "$worker_state" =~ ^(dead|exited|restarting)$ ]] ||
    [[ "$app_health" == "unhealthy" || "$worker_health" == "unhealthy" ]]; then
    docker_cmd logs --tail 200 tg-lottery-app >&2 || true
    docker_cmd logs --tail 200 tg-lottery-worker >&2 || true
    exit 1
  fi
  elapsed=$(( $(date +%s) - started_at ))
  if (( elapsed >= timeout_seconds )); then
    docker_cmd logs --tail 200 tg-lottery-app >&2 || true
    docker_cmd logs --tail 200 tg-lottery-worker >&2 || true
    exit 1
  fi
  printf 'Waiting for services (%ss/%ss): app=%s/%s worker=%s/%s\n' \
    "$elapsed" "$timeout_seconds" "${app_state:-none}" "${app_health:-none}" \
    "${worker_state:-none}" "${worker_health:-none}"
  sleep 5
done

compose ps
echo "TG Lottery deployment check completed"
