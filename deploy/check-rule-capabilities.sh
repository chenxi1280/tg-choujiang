#!/usr/bin/env bash
set -euo pipefail
release_target="${1:?target release directory required}"
source "${release_target}/deploy/docker-env.sh"
ensure_runtime_env
compose pull app
candidate_capabilities="$(compose run --rm --no-deps -T app python -m tg_lottery.rule_capabilities --describe)"
docker_cmd exec tg-lottery-worker python -m tg_lottery.rule_capabilities --check "$candidate_capabilities"
