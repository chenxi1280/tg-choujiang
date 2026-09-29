#!/usr/bin/env bash
set -euo pipefail

mode="${1:-all}"
shift "$(( $# > 0 ? 1 : 0 ))"
lock_file=/opt/tg-lottery-test/run.lock
password_file=/opt/tg-lottery-test/postgres_password
instance_id=shanghai-lottery-dedicated-20260929

exec 9>"$lock_file"
flock -w 900 9
work_dir="$(mktemp -d /tmp/tg-lottery-test.XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT
tar -xf - -C "$work_dir"
docker build --network host \
  --build-arg HTTP_PROXY=http://127.0.0.1:7890 \
  --build-arg HTTPS_PROXY=http://127.0.0.1:7890 \
  -f "$work_dir/deploy/test-runner.Dockerfile" \
  -t tg-lottery-test-runner:current "$work_dir"
docker run --rm --network container:tg-lottery-test-postgres \
  --memory 2g --cpus 2 \
  -e "TEST_DATABASE_INSTANCE_ID=$instance_id" \
  -v "$password_file:/run/secrets/postgres_password:ro" \
  tg-lottery-test-runner:current "$mode" "$@"
