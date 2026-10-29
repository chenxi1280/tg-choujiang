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
# Quiesce both public Telegram consumers before enabling a new runtime policy.
# Otherwise the new worker could persist its activation boundary while an old
# consumer still sends messages using the previous retention policy.
compose stop app worker
if compose --profile tools run --rm --no-deps migrate; then
  :
else
  migration_status=$?
  echo "Migration failed; restarting the previous app/worker containers" >&2
  if ! compose start app worker; then
    echo "Previous app/worker restart failed; manual recovery required" >&2
  fi
  exit "$migration_status"
fi

echo "==> Starting app and worker"
integration_env="${LOTTERY_INTEGRATION_ENV_FILE:-${SHARED_DIR}/lottery-integration.env}"
integration_enabled=false
if [[ -f "$integration_env" ]]; then
  export LOTTERY_INTEGRATION_ENV_FILE="$integration_env"
  integration_enabled=true
  compose --profile integration up -d --no-build --remove-orphans app worker integration
else
  compose up -d --no-build --remove-orphans app worker
fi

timeout_seconds="${TG_LOTTERY_HEALTH_TIMEOUT_SECONDS:-600}"
started_at="$(date +%s)"
while true; do
  app_state="$(docker_cmd inspect tg-lottery-app --format '{{.State.Status}}' 2>/dev/null || true)"
  worker_state="$(docker_cmd inspect tg-lottery-worker --format '{{.State.Status}}' 2>/dev/null || true)"
  app_health="$(docker_cmd inspect tg-lottery-app --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"
  worker_health="$(docker_cmd inspect tg-lottery-worker --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"
  integration_health="healthy"
  integration_state="running"
  if [[ "$integration_enabled" == true ]]; then
    integration_state="$(docker_cmd inspect tg-lottery-integration --format '{{.State.Status}}' 2>/dev/null || true)"
    integration_health="$(docker_cmd inspect tg-lottery-integration --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || true)"
  fi
  if [[ "$app_health" == "healthy" && "$worker_health" == "healthy" && "$integration_health" == "healthy" ]]; then
    break
  fi
  if [[ "$app_state" =~ ^(dead|exited|restarting)$ ]] ||
    [[ "$worker_state" =~ ^(dead|exited|restarting)$ ]] ||
    [[ "$integration_state" =~ ^(dead|exited|restarting)$ ]] ||
    [[ "$app_health" == "unhealthy" || "$worker_health" == "unhealthy" || "$integration_health" == "unhealthy" ]]; then
    docker_cmd logs --tail 200 tg-lottery-app >&2 || true
    docker_cmd logs --tail 200 tg-lottery-worker >&2 || true
    [[ "$integration_enabled" == true ]] && docker_cmd logs --tail 50 tg-lottery-integration >&2 || true
    exit 1
  fi
  elapsed=$(( $(date +%s) - started_at ))
  if (( elapsed >= timeout_seconds )); then
    docker_cmd logs --tail 200 tg-lottery-app >&2 || true
    docker_cmd logs --tail 200 tg-lottery-worker >&2 || true
    [[ "$integration_enabled" == true ]] && docker_cmd logs --tail 50 tg-lottery-integration >&2 || true
    exit 1
  fi
  printf 'Waiting for services (%ss/%ss): app=%s/%s worker=%s/%s\n' \
    "$elapsed" "$timeout_seconds" "${app_state:-none}" "${app_health:-none}" \
    "${worker_state:-none}" "${worker_health:-none}"
  sleep 5
done

compose ps
echo "TG Lottery deployment check completed"
