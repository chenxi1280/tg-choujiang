#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/docker-env.sh"

ensure_runtime_env

container_status() {
  local role="$1" observed state health
  if ! observed="$(docker_cmd inspect "tg-lottery-${role}" \
      --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')"; then
    printf '%s status unavailable\n' "$role" >&2
    return 1
  fi
  IFS='|' read -r state health <<< "$observed"
  printf '%s %s %s\n' "$role" "$state" "$health"
  [[ "$state" == running && "$health" == healthy ]]
}

status=0
container_status app || status=$?
container_status worker || status=$?
docker_cmd exec tg-lottery-app python -m tg_lottery.healthcheck --role app || status=$?
docker_cmd exec tg-lottery-worker python -m tg_lottery.healthcheck --role worker || status=$?
docker_cmd exec tg-lottery-worker alembic current || status=$?
docker_cmd exec tg-lottery-worker python -m tg_lottery.ops.delivery_health || status=$?
if (( status == 0 )); then
  echo 'CHECK_STATUS=PASSED'
else
  echo 'CHECK_STATUS=FAILED'
fi
exit "$status"
