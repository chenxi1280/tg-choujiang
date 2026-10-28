#!/usr/bin/env bash
set -euo pipefail

mode="${1:-all}"
shift "$(( $# > 0 ? 1 : 0 ))"
lock_file=/opt/tg-lottery-test/run.lock
password_file=/opt/tg-lottery-test/postgres_password
instance_id=shanghai-lottery-dedicated-20260929

exec 9>"$lock_file"
flock -w 900 9
# A disconnected Docker client can release the lock before its container exits.
# Refuse to touch the test database while an earlier executor might still run.
named_runner="$(docker ps -aq --filter 'name=^/tg-lottery-test-executor$')"
running_ids="$(docker ps -q)"
conflicting_runners=""
if [[ -n "$running_ids" ]]; then
  mapfile -t container_ids <<< "$running_ids"
  database_id="$(docker inspect --format '{{.Id}}' tg-lottery-test-postgres)"
  conflicting_runners="$(docker inspect --format '{{.Id}} {{.Config.Image}} {{.HostConfig.NetworkMode}}' "${container_ids[@]}" |
    awk -v database_id="$database_id" \
      '$2 == "tg-lottery-test-runner:current" || $3 == "container:" database_id || $3 == "container:tg-lottery-test-postgres" {print $1}')"
fi
if [[ -n "$named_runner" || -n "$conflicting_runners" ]]; then
  echo "A Shanghai test executor container still exists; refusing concurrent database access" >&2
  exit 2
fi
work_dir="$(mktemp -d /tmp/tg-lottery-test.XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT
tar -xf - -C "$work_dir"
proxy="${TG_LOTTERY_TEST_BUILD_PROXY:-}"
if [[ -n "$proxy" && ! "$proxy" =~ ^http://127\.0\.0\.1:[0-9]{1,5}$ ]]; then
  echo "TG_LOTTERY_TEST_BUILD_PROXY must be a loopback HTTP proxy" >&2
  exit 2
fi
docker build --network host \
  --build-arg "HTTP_PROXY=$proxy" --build-arg "http_proxy=$proxy" \
  --build-arg "HTTPS_PROXY=$proxy" --build-arg "https_proxy=$proxy" \
  --build-arg ALL_PROXY= --build-arg all_proxy= \
  -f "$work_dir/deploy/test-runner.Dockerfile" \
  -t tg-lottery-test-runner:current "$work_dir"
docker run --rm --name tg-lottery-test-executor --network container:tg-lottery-test-postgres \
  --memory 2g --cpus 2 \
  -e "TEST_DATABASE_INSTANCE_ID=$instance_id" \
  -v "$password_file:/run/secrets/postgres_password:ro" \
  tg-lottery-test-runner:current "$mode" "$@"
