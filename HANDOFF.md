# Hermes agent handoff

Continue this existing Car Thing agent monitor project. Read `README.md` first for installation, controls, telemetry format, and restore instructions.

## Current code

- `index.html`: standalone UI for Hermes, Claude, and Codex, with themes, touch navigation, activity, and token views.
- `config.js`: status endpoints, currently using device loopback port 8892.
- `bridge/server.py`: Python standard-library HTTP status bridge with persisted snapshots and bearer authentication for writes.
- `hooks/report.py`, `hooks/forwarder.py`: Mac Claude/Codex status collection and forwarding.
- `scripts/install-mac-hooks.py`: Mac hook and forwarder installation/removal.
- `pi/` and `scripts/install-pi.sh`: Raspberry Pi systemd services and USB `adb reverse` link.
- `scripts/install-standalone.sh`, `scripts/restore-deskthing.sh`: install UI or restore DeskThing.
- `output/`: existing visual previews; these do not establish current device behavior.

## Work to finish / verify

1. Establish the user's current Car Thing and Pi setup, then verify the documented end-to-end installation and live Claude/Codex telemetry. Hardware runtime behavior was not checked during repository creation.
2. Connect real Hermes telemetry. The UI and bridge support Hermes, but the existing Mac hooks collect Claude/Codex status; implement the Hermes producer for the user's actual agent runtime.
3. Verify switching, themes, token details, reconnect behavior, and memory use on the Car Thing. The design aims to avoid the DeskThing shell plus iframe memory overhead.
4. Resolve the documented outlet-power limitation if required: README reports no active Wi-Fi interface on the current firmware. Live telemetry currently depends on a network path through the USB-connected Pi.

## Setup notes

- No npm dependencies or build step are present. For a local UI preview, run `python3 -m http.server 8000` and open `http://127.0.0.1:8000`.
- Use `config/client.example.json` as a configuration reference. Supply a fresh shared token during setup; keep real credentials out of Git.
- The standalone installer defaults to an existing device serial. Pass the correct serial as its first argument after checking `adb devices`.
- Read device installation and restore scripts before using them; they change Chromium startup through supervisor configuration.
- See `README.md` for generating the Pi transfer archive. Generated archives and local browser logs are ignored by Git.

Repository creation preserves the existing implementation; the finishing priorities above are based on the source and README, not a new hardware test.
