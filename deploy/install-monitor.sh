#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_dir="${BASE_DIR:-/data/tg-lottery}"
sudo -n test -s /etc/tg-lottery/service-health.env
for unit in tg-lottery-healthcheck.service tg-lottery-healthcheck.timer; do
  sed "s|/data/tg-lottery|${base_dir}|g" "$script_dir/systemd/$unit" |
    sudo -n tee "/etc/systemd/system/$unit" >/dev/null
done
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now tg-lottery-healthcheck.timer
