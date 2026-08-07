#!/usr/bin/env bash

set -euo pipefail

USER_NAME="${USER_NAME:-root}"
HOST="${HOST:-}"
BASE_DIR="${BASE_DIR:-/data/tg-lottery}"
REF_NAME="${REF_NAME:-HEAD}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
EXPECTED_BRANCHES="${EXPECTED_BRANCHES:-release}"
CONFIRM_PRODUCTION_DEPLOY="${CONFIRM_PRODUCTION_DEPLOY:-0}"
IMAGE_NAMESPACE="${IMAGE_NAMESPACE:-ghcr.io/chenxi1280}"
RELEASE_SSH_ATTEMPTS="${RELEASE_SSH_ATTEMPTS:-3}"
RELEASE_SSH_RETRY_DELAY="${RELEASE_SSH_RETRY_DELAY:-10}"
SSH_OPTS=(-o "ConnectTimeout=${SSH_CONNECT_TIMEOUT:-20}" -o "ServerAliveInterval=30" -o "ServerAliveCountMax=10")

usage() {
  cat <<'EOF'
Usage:
  bash deploy/release.sh --host <host> [options]

Options:
  --host HOST
  --user USER
  --base-dir DIR        Default /data/tg-lottery
  --ref REF             Default HEAD
  --allow-dirty
  --confirm-production-deploy
  --branch-list "release main master"
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --base-dir) BASE_DIR="$2"; shift 2 ;;
    --ref) REF_NAME="$2"; shift 2 ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --confirm-production-deploy) CONFIRM_PRODUCTION_DEPLOY=1; shift ;;
    --branch-list) EXPECTED_BRANCHES="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ -z "$HOST" ]]; then
  usage >&2
  exit 1
fi

require_command() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }
}

run_with_retries() {
  local label="$1"; shift
  local attempt status
  for ((attempt = 1; attempt <= RELEASE_SSH_ATTEMPTS; attempt++)); do
    if "$@"; then return 0; fi
    status=$?
    if (( attempt == RELEASE_SSH_ATTEMPTS )); then
      echo "${label} failed after ${RELEASE_SSH_ATTEMPTS} attempts" >&2
      return "$status"
    fi
    sleep "$RELEASE_SSH_RETRY_DELAY"
  done
}

require_command git
require_command ssh
require_command scp
require_command mktemp

if [[ "$BASE_DIR" == "/data/tg-lottery" && "${GITHUB_ACTIONS:-}" != "true" && "$CONFIRM_PRODUCTION_DEPLOY" != "1" ]]; then
  echo "Refusing local production deploy without --confirm-production-deploy" >&2
  exit 1
fi

current_branch="$(git branch --show-current)"
branch_allowed=0
for branch in $EXPECTED_BRANCHES; do
  [[ "$current_branch" == "$branch" ]] && branch_allowed=1
done
if [[ "$branch_allowed" != "1" ]]; then
  echo "Refusing to release from branch '${current_branch}'. Allowed: ${EXPECTED_BRANCHES}" >&2
  exit 1
fi

if [[ "$ALLOW_DIRTY" != "1" && -n "$(git status --porcelain)" ]]; then
  echo "Working tree is dirty. Commit or stash changes first, or rerun with --allow-dirty." >&2
  exit 1
fi

short_sha="$(git rev-parse --short "$REF_NAME")"
full_sha="$(git rev-parse "$REF_NAME")"
image_tag="${IMAGE_TAG:-$full_sha}"
TG_LOTTERY_APP_IMAGE="${TG_LOTTERY_APP_IMAGE:-${IMAGE_NAMESPACE}/tg-lottery-app:${image_tag}}"
release_id="$(date '+%Y%m%d%H%M%S')_${short_sha}"
archive_path="$(mktemp "/tmp/tg-lottery-release-${release_id}.XXXXXX.tar.gz")"
image_env_path="$(mktemp "/tmp/tg-lottery-image-env-${release_id}.XXXXXX.env")"
remote_archive="${BASE_DIR}/incoming/${release_id}.tar.gz"
remote_image_env="${BASE_DIR}/incoming/${release_id}.image.env"
remote_release_dir="${BASE_DIR}/releases/${release_id}"

trap 'rm -f "$archive_path" "$image_env_path"' EXIT

cat >"$image_env_path" <<EOF
TG_LOTTERY_APP_IMAGE=${TG_LOTTERY_APP_IMAGE}
RELEASE_SHA=${full_sha}
RELEASE_ID=${release_id}
EOF

git archive --format=tar.gz --output "$archive_path" "$REF_NAME"

run_with_retries "Preparing remote directories" \
  ssh "${SSH_OPTS[@]}" "${USER_NAME}@${HOST}" "mkdir -p '${BASE_DIR}/incoming' '${BASE_DIR}/releases'"
run_with_retries "Uploading release archive" \
  scp "${SSH_OPTS[@]}" "$archive_path" "${USER_NAME}@${HOST}:${remote_archive}"
run_with_retries "Uploading image env" \
  scp "${SSH_OPTS[@]}" "$image_env_path" "${USER_NAME}@${HOST}:${remote_image_env}"

remote_env=""
[[ -n "${GHCR_USERNAME:-}" ]] && remote_env+=" GHCR_USERNAME=$(printf '%q' "$GHCR_USERNAME")"
[[ -n "${GHCR_TOKEN:-}" ]] && remote_env+=" GHCR_TOKEN=$(printf '%q' "$GHCR_TOKEN")"

ssh "${SSH_OPTS[@]}" "${USER_NAME}@${HOST}" "
  set -euo pipefail
  mkdir -p '${remote_release_dir}'
  tar -xzf '${remote_archive}' -C '${remote_release_dir}'
  mv '${remote_image_env}' '${remote_release_dir}/.image.env'
  BASE_DIR='${BASE_DIR}' RELEASE_ID='${release_id}'${remote_env} bash '${remote_release_dir}/deploy/server-install-release.sh' --base-dir '${BASE_DIR}' --release-dir '${remote_release_dir}' --release-id '${release_id}'
"

echo "Release ${release_id} deployed"
