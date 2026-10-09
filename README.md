# Agent Monitor for Car Thing

For continuation by the Hermes agent, see [HANDOFF.md](HANDOFF.md).

A single-renderer, low-memory monitor for Hermes, Claude, and Codex. It is designed for the Car Thing's 488 MB RAM limit and avoids the DeskThing shell plus iframe combination that causes Chromium to be killed by `earlyoom`.

## Controls

- Tap one of the three lower-left agent icons to switch screens.
- Tap the sun/moon button beside them to toggle dark and light mode.
- Swipe left or right anywhere to move between agents.
- Tap the large activity panel to toggle activity and token detail.
- Left/right keyboard or mapped hardware actions also switch agents; `T` toggles the theme.

## Status Sources

Edit `config.js` to set one JSON status endpoint per agent. Only the visible agent is polled. Failed requests use exponential backoff up to five minutes.

The normalizer accepts fields such as:

```json
{
  "gateway": "online",
  "activity": "working",
  "provider": "OpenAI",
  "model": "gpt-5",
  "latencyMs": 42,
  "tokens": { "input": 1200, "output": 340, "total": 1540 },
  "tools": { "active": ["shell", "search"] },
  "message": "Running a task"
}
```

## Claude and Codex Bridge

The monitor includes a dependency-free status bridge designed to run on a Raspberry Pi. Claude and Codex hooks write small status snapshots on the Mac. One launchd forwarder sends only the newest snapshot to the Pi, and the Pi exposes it to the Car Thing through `adb reverse`.

Prompt text and tool arguments are not forwarded.

The bridge listens on `127.0.0.1` by default, which is all `adb reverse` needs, and refuses to start without a write token. Prefer `AGENT_MONITOR_TOKEN_FILE` pointing at a `chmod 0600` file; `AGENT_MONITOR_TOKEN` still works. The Mac forwarder below needs the bridge reachable from the Mac, so it requires setting `AGENT_MONITOR_HOST` to a private address (for example a Tailscale IP). Do not bind it to a public interface.

## Hermes Telemetry

`hooks/hermes_status.py` polls Hermes's public liveness endpoint (`GET http://127.0.0.1:9119/api/status`: gateway state and active agent count, no chat content or secrets) every 15 seconds and posts online/offline and working/idle to the bridge's `hermes` screen. It reports no model or token counts because that endpoint has neither.

```bash
AGENT_MONITOR_TOKEN_FILE=~/.local/state/agent-monitor/token python3 hooks/hermes_status.py
```

Run the tests with `python3 -m unittest discover -s tests -v`.

Install the Mac hooks and forwarder:

```bash
python3 scripts/install-mac-hooks.py \
  --base-url http://hermes-pi.example:8892 \
  --token YOUR_SHARED_TOKEN
```

On the Pi, clone or copy this project and run:

```bash
sudo ./scripts/install-pi.sh YOUR_SHARED_TOKEN
```

To make a small transfer archive on the Mac:

```bash
./scripts/package-pi.sh
scp output/agent-monitor-pi.tar.gz YOUR_PI_USER@hermes-pi.example:~
```

Then, after signing into the Pi:

```bash
mkdir -p ~/agent-monitor
tar -xzf ~/agent-monitor-pi.tar.gz -C ~/agent-monitor
cd ~/agent-monitor
sudo ./scripts/install-pi.sh YOUR_SHARED_TOKEN
```

After the Car Thing is connected to the Pi by USB, install the latest monitor UI:

```bash
./scripts/install-standalone.sh
```

Check the Pi services:

```bash
systemctl status agent-monitor-bridge.service
systemctl status agent-monitor-link.service
curl http://127.0.0.1:8892/health
adb devices
adb reverse --list
```

Codex snapshots hook configuration at session start. Start a new Codex task after installation and approve the project hook through `/hooks` if prompted.

Remove the Mac hooks and forwarder without touching other Claude or Codex hooks:

```bash
python3 scripts/install-mac-hooks.py --remove
```

## Device Modes

Install standalone mode without deleting DeskThing:

```bash
./scripts/install-standalone.sh
```

Restore the original DeskThing display:

```bash
./scripts/restore-deskthing.sh
```

The scripts keep the DeskThing webapp untouched and only change Chromium's startup page. The original supervisor config is backed up once at `/etc/supervisord.conf.deskthing-backup`.

## Outlet Power

The current Car Thing firmware has no active Wi-Fi network interface, so the standalone UI remains stable and navigable on outlet power but cannot receive live remote telemetry until the device has a network path. Empty source URLs intentionally show a local standby state without retry traffic.

## Push Mode (no `adb reverse`)

On this Car Thing, `adb reverse` succeeds and then the device's adb connection drops off USB until it is replugged, so the device cannot poll the Pi. Instead, `pi/push-status.sh` reads the bridge's Hermes status every 15 seconds and copies it to `/tmp/agent-monitor/hermes.json` on the device with plain `adb push` (an in-memory tmpfs, cleared on reboot):

```bash
CARTHING_SERIAL=<serial from adb devices> bash pi/push-status.sh
```

Point the device's `config.js` at the pushed file, for example `statusUrl: "file:///tmp/agent-monitor/hermes.json"`. The page reads `file:` sources with `XMLHttpRequest`, because the device's Chromium 69 rejects `fetch()` for `file:` URLs; it already runs with `--allow-file-access-from-files`. HTTP sources still use `fetch()`.
