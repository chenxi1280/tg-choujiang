#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if lsof -nP -iTCP:25433 -sTCP:LISTEN >/dev/null; then
  python "$root_dir/scripts/check_shanghai_test_db.py"
  echo "Existing Shanghai test database tunnel is ready"
  exit 0
fi

ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -fN \
  -L 127.0.0.1:25433:127.0.0.1:15433 shanghai-tg-test-db
python "$root_dir/scripts/check_shanghai_test_db.py"
