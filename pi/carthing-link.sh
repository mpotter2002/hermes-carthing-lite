#!/usr/bin/env bash
set -u

PORT="${AGENT_MONITOR_PORT:-8892}"
LAST_SERIAL=""

adb start-server >/dev/null 2>&1 || true

while true; do
  SERIAL="$(adb devices 2>/dev/null | awk 'NR > 1 && $2 == "device" { print $1; exit }')"
  if [[ -n "$SERIAL" ]]; then
    if [[ "$SERIAL" != "$LAST_SERIAL" ]]; then
      echo "Car Thing connected as $SERIAL"
      LAST_SERIAL="$SERIAL"
    fi
    adb -s "$SERIAL" reverse "tcp:$PORT" "tcp:$PORT" >/dev/null 2>&1 || true
  else
    if [[ -n "$LAST_SERIAL" ]]; then
      echo "Car Thing disconnected"
      LAST_SERIAL=""
    fi
  fi
  sleep 5
done
