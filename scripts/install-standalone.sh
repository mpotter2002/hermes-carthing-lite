#!/usr/bin/env bash
set -euo pipefail

DEVICE_ID="${1:-8550R188QP16}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="/usr/share/qt-superbird-app/agent-monitor"
SUPERVISOR_CONFIG="/etc/supervisord.conf"
BACKUP_CONFIG="/etc/supervisord.conf.deskthing-backup"

adb -s "$DEVICE_ID" get-state >/dev/null
adb -s "$DEVICE_ID" shell "test -f '$BACKUP_CONFIG' || cp '$SUPERVISOR_CONFIG' '$BACKUP_CONFIG'"
adb -s "$DEVICE_ID" shell "mkdir -p '$TARGET_DIR/assets'"
adb -s "$DEVICE_ID" push "$PROJECT_DIR/index.html" "$TARGET_DIR/index.html" >/dev/null
adb -s "$DEVICE_ID" push "$PROJECT_DIR/config.js" "$TARGET_DIR/config.js" >/dev/null
adb -s "$DEVICE_ID" push "$PROJECT_DIR/assets/hermes-portrait-inner.png" "$TARGET_DIR/assets/hermes-portrait-inner.png" >/dev/null
adb -s "$DEVICE_ID" push "$PROJECT_DIR/assets/claude.svg" "$TARGET_DIR/assets/claude.svg" >/dev/null
adb -s "$DEVICE_ID" push "$PROJECT_DIR/assets/codex.svg" "$TARGET_DIR/assets/codex.svg" >/dev/null

adb -s "$DEVICE_ID" shell "sed -i 's#--app=file:///usr/share/qt-superbird-app/[^ ]*/index.html#--app=file://$TARGET_DIR/index.html#' '$SUPERVISOR_CONFIG'"
adb -s "$DEVICE_ID" shell "supervisorctl reread >/dev/null; supervisorctl update chromium >/dev/null; supervisorctl restart chromium >/dev/null"

echo "Standalone agent monitor installed on $DEVICE_ID."
