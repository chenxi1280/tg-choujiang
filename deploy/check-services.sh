#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/docker-env.sh"

ensure_runtime_env

docker_cmd inspect tg-lottery-app --format 'app {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker_cmd inspect tg-lottery-worker --format 'worker {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker_cmd exec tg-lottery-app python -m tg_lottery.healthcheck --role app
docker_cmd exec tg-lottery-worker python -m tg_lottery.healthcheck --role worker
docker_cmd exec tg-lottery-worker alembic current

IFS='|' read -r unknown_deliveries unprojected_confirmations failed_jobs failed_updates < <(
  docker_cmd exec app-infra-postgres psql -U postgres -d tg_lottery -tA -F '|' -c \
    "SELECT
       (SELECT count(*) FROM telegram_outbox WHERE state = 'unknown_hold'),
       (SELECT count(*) FROM telegram_outbox WHERE state = 'confirmed' AND projected_at IS NULL),
       (SELECT count(*) FROM scheduled_jobs WHERE state IN ('blocked', 'failed_terminal')),
       (SELECT count(*) FROM telegram_updates WHERE state = 'failed');"
)

if (( unknown_deliveries > 0 || unprojected_confirmations > 0 || failed_jobs > 0 || failed_updates > 0 )); then
  printf 'STATUS=DEGRADED unknown_deliveries=%s unprojected_confirmations=%s failed_jobs=%s failed_updates=%s\n' \
    "$unknown_deliveries" "$unprojected_confirmations" "$failed_jobs" "$failed_updates" >&2
  exit 1
fi

echo "STATUS=HEALTHY"
