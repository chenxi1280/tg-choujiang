#!/usr/bin/env bash
set -euo pipefail

ssh_target="${SHANGHAI_TEST_SSH_TARGET:-shanghai-tg-test-db}"
test_mode="${1:-all}"
if [[ "$test_mode" != "all" && "$test_mode" != "migrate" && "$test_mode" != "pytest" ]]; then
  echo "Unknown test mode: $test_mode" >&2
  exit 2
fi
shift "$(( $# > 0 ? 1 : 0 ))"
remote_args=""
if (( $# > 0 )); then
  printf -v remote_args '%q ' "$@"
fi

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"
COPYFILE_DISABLE=1 tar --format=ustar -cf - \
  --exclude='._*' --exclude='__pycache__' --exclude='*.pyc' \
  pyproject.toml alembic.ini src tests alembic scripts \
  deploy/test-runner.Dockerfile deploy/install_test_dependencies.py \
  deploy/run_remote_tests.py |
  ssh -o BatchMode=yes "$ssh_target" \
    "sudo -n /opt/tg-lottery-test/run.sh '$test_mode' $remote_args"
