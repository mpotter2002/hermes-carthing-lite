#!/usr/bin/env bash
# Push the bridge's Hermes status to the Car Thing every 15 s over plain `adb push`.
# This Car Thing's adbd drops off USB after `adb reverse`, so the device never connects back to the Pi.
set -u
SERIAL="${CARTHING_SERIAL:?set CARTHING_SERIAL from adb devices}"
BRIDGE="${AGENT_MONITOR_BRIDGE_URL:-http://127.0.0.1:8892}"
REMOTE_DIR="/tmp/agent-monitor"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
while true; do
  if curl -fsS --max-time 3 "$BRIDGE/status/hermes" -o "$TMP" \
     && adb -s "$SERIAL" shell "mkdir -p $REMOTE_DIR" >/dev/null 2>&1 \
     && adb -s "$SERIAL" push "$TMP" "$REMOTE_DIR/hermes.json.tmp" >/dev/null 2>&1 \
     && adb -s "$SERIAL" shell "mv $REMOTE_DIR/hermes.json.tmp $REMOTE_DIR/hermes.json" >/dev/null 2>&1; then
    [ "${last:-}" = ok ] || { echo "$(date -Is) pushing"; last=ok; }
  else
    [ "${last:-}" = fail ] || { echo "$(date -Is) push failed (device unplugged or bridge down); retrying"; last=fail; }
  fi
  sleep 15
done
