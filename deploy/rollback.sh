#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash deploy/rollback.sh <release_id>" >&2
  exit 1
fi

BASE_DIR="${BASE_DIR:-/data/tg-lottery}"
RELEASE_ID="$1"
RELEASE_DIR="${BASE_DIR}/releases/${RELEASE_ID}"

if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "Release not found: ${RELEASE_DIR}" >&2
  exit 1
fi

# The current capable guard must inspect the target image before its older installer can run.
guard_script="${BASE_DIR}/current/deploy/check-rule-capabilities.sh"
if [[ ! -f "$guard_script" ]]; then
  echo "Current release has no rule capability guard; rollback refused" >&2
  exit 1
fi
bash "$guard_script" "$RELEASE_DIR"

BASE_DIR="$BASE_DIR" RELEASE_ID="$RELEASE_ID" bash "${RELEASE_DIR}/deploy/server-install-release.sh" \
  --base-dir "$BASE_DIR" \
  --release-dir "$RELEASE_DIR" \
  --release-id "$RELEASE_ID"
