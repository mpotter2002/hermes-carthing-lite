#!/usr/bin/env bash
set -euo pipefail

DEVICE_ID="${1:-8550R188QP16}"
SUPERVISOR_CONFIG="/etc/supervisord.conf"
BACKUP_CONFIG="/etc/supervisord.conf.deskthing-backup"

adb -s "$DEVICE_ID" get-state >/dev/null
adb -s "$DEVICE_ID" shell "test -f '$BACKUP_CONFIG'"
adb -s "$DEVICE_ID" shell "cp '$BACKUP_CONFIG' '$SUPERVISOR_CONFIG'"
adb -s "$DEVICE_ID" shell "supervisorctl reread >/dev/null; supervisorctl update chromium >/dev/null; supervisorctl restart chromium >/dev/null"

echo "DeskThing display restored on $DEVICE_ID."
