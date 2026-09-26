#!/usr/bin/env bash
# Schedule the weekly bird email on this Pi: Sunday mornings at 7:30, covering the
# previous Sunday to Saturday.
# Mirrors run.sh's install_service: a system unit that runs as this user from
# this checkout. Persistent=true sends a missed week once the Pi is back on.
#
#   bash extras/install-weekly-email.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SETTINGS="$HOME/.config/fugleramme/weekly-email.json"
UV_BIN="$(command -v uv || echo "$HOME/.local/bin/uv")"

if [[ ! -f "$SETTINGS" ]]; then
  echo "no settings at $SETTINGS - copy weekly-email.json there first" >&2
  exit 1
fi
chmod 600 "$SETTINGS"

sudo tee /etc/systemd/system/fugleramme-weekly-email.service >/dev/null <<EOF
[Unit]
Description=Fugleramme weekly bird email
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO_ROOT
ExecStart=$UV_BIN run python extras/weekly_email.py --send
EOF

sudo tee /etc/systemd/system/fugleramme-weekly-email.timer >/dev/null <<EOF
[Unit]
Description=Send the weekly bird email on Sunday mornings

[Timer]
OnCalendar=Sun *-*-* 07:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now fugleramme-weekly-email.timer
systemctl list-timers fugleramme-weekly-email.timer --no-pager
