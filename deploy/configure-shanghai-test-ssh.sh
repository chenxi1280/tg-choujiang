#!/usr/bin/env bash
set -euo pipefail

: "${SHANGHAI_TEST_HOST:?SHANGHAI_TEST_HOST is required}"
: "${SHANGHAI_TEST_SSH_PRIVATE_KEY:?SHANGHAI_TEST_SSH_PRIVATE_KEY is required}"
: "${SHANGHAI_TEST_KNOWN_HOSTS:?SHANGHAI_TEST_KNOWN_HOSTS is required}"
if [[ ! "$SHANGHAI_TEST_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "SHANGHAI_TEST_HOST must be a hostname or IP address" >&2
  exit 2
fi

umask 077
mkdir -p "$HOME/.ssh"
printf '%s\n' "$SHANGHAI_TEST_SSH_PRIVATE_KEY" > "$HOME/.ssh/shanghai-test-key"
printf '%s\n' "$SHANGHAI_TEST_KNOWN_HOSTS" > "$HOME/.ssh/known_hosts"
cat > "$HOME/.ssh/config" <<EOF
Host shanghai-tg-test-db
  HostName $SHANGHAI_TEST_HOST
  User ubuntu
  IdentityFile $HOME/.ssh/shanghai-test-key
  IdentitiesOnly yes
  StrictHostKeyChecking yes
EOF
