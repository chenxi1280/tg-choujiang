#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="${BASE_DIR:-/data/tg-lottery}"
ENV_FILE="${ENV_FILE:-${BASE_DIR}/shared/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

docker inspect tg-lottery-app --format 'app {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker inspect tg-lottery-worker --format 'worker {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker exec tg-lottery-app python -m tg_lottery.healthcheck --role app
docker exec tg-lottery-worker python -m tg_lottery.healthcheck --role worker
docker exec tg-lottery-worker alembic current

echo "STATUS=HEALTHY"

