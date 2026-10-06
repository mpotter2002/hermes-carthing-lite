#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

TOKEN="${1:-}"
if [[ -z "$TOKEN" ]]; then
  echo "Usage: sudo ./scripts/install-pi.sh <shared-token>" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3
fi

if ! command -v adb >/dev/null 2>&1; then
  apt-get update
  apt-get install -y adb
fi

install -d -m 0755 /opt/agent-monitor
install -m 0755 "$PROJECT_DIR/bridge/server.py" /opt/agent-monitor/server.py
install -m 0755 "$PROJECT_DIR/pi/carthing-link.sh" /opt/agent-monitor/carthing-link.sh
install -m 0644 "$PROJECT_DIR/pi/agent-monitor-bridge.service" /etc/systemd/system/agent-monitor-bridge.service
install -m 0644 "$PROJECT_DIR/pi/agent-monitor-link.service" /etc/systemd/system/agent-monitor-link.service
printf 'AGENT_MONITOR_TOKEN=%s\n' "$TOKEN" > /etc/agent-monitor.env
chmod 0600 /etc/agent-monitor.env

systemctl daemon-reload
systemctl enable --now agent-monitor-bridge.service
systemctl enable --now agent-monitor-link.service

echo
echo "Agent monitor services installed."
systemctl --no-pager --full status agent-monitor-bridge.service || true
echo
curl --fail --silent http://127.0.0.1:8892/health
echo
