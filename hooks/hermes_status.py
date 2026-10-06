#!/usr/bin/env python3
"""Post Hermes gateway status to the Car Thing bridge.

Reads Hermes's public liveness endpoint (GET /api/status: gateway state and active agent count,
no session content or secrets) and posts a minimal status for the "hermes" screen. It does not
read transcripts, and it does not report a model or token counts because /api/status has neither.

Usage:
  AGENT_MONITOR_TOKEN_FILE=~/.local/state/agent-monitor/token python3 hooks/hermes_status.py
  python3 hooks/hermes_status.py --once        # single poll, exit 0 if posted
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_HERMES_URL = "http://127.0.0.1:9119/api/status"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8892"
PROVIDER = "Nous Research"
RUNNING = True


def read_token(path: str) -> str:
    token_path = Path(path).expanduser()
    try:
        mode = stat.S_IMODE(token_path.stat().st_mode)
        value = token_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        sys.exit(f"Cannot read token file {token_path}: {error.strerror}")
    if mode & 0o077:
        sys.exit(f"Token file {token_path} must not be readable by group/others (chmod 0600).")
    if not value:
        sys.exit(f"Token file {token_path} is empty.")
    return value


def map_status(raw: dict) -> dict:
    running = bool(raw.get("gateway_running"))
    if not running:
        state = str(raw.get("gateway_state") or "stopped")[:40]
        return {"gateway": "offline", "activity": "idle", "provider": PROVIDER,
                "message": f"Gateway {state}", "tools": {"active": []}}
    try:
        agents = max(0, int(raw.get("active_agents") or 0))
    except (TypeError, ValueError):
        agents = 0
    busy = agents > 0 or bool(raw.get("gateway_busy"))
    if busy and agents:
        message = f"{agents} agent{'s' if agents != 1 else ''} working"
    elif busy:
        message = "Working"
    else:
        message = "Ready"
    return {"gateway": "online", "activity": "working" if busy else "idle",
            "provider": PROVIDER, "message": message, "tools": {"active": []}}


def unavailable_status() -> dict:
    return {"gateway": "offline", "activity": "idle", "provider": PROVIDER,
            "message": "Hermes status unavailable", "tools": {"active": []}}


def fetch_hermes(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            raw = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return unavailable_status()
    return map_status(raw) if isinstance(raw, dict) else unavailable_status()


def post_status(bridge_url: str, token: str, status: dict) -> bool:
    request = urllib.request.Request(
        f"{bridge_url.rstrip('/')}/status/hermes",
        data=json.dumps(status, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def run_once(hermes_url: str, bridge_url: str, token: str) -> bool:
    return post_status(bridge_url, token, fetch_hermes(hermes_url))


def stop(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hermes-url", default=os.environ.get("HERMES_STATUS_URL", DEFAULT_HERMES_URL))
    parser.add_argument("--bridge-url", default=os.environ.get("AGENT_MONITOR_BRIDGE_URL", DEFAULT_BRIDGE_URL))
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    token_file = os.environ.get("AGENT_MONITOR_TOKEN_FILE", "")
    if not token_file:
        sys.exit("Set AGENT_MONITOR_TOKEN_FILE to a 0600 file holding the bridge token.")
    token = read_token(token_file)

    if args.once:
        return 0 if run_once(args.hermes_url, args.bridge_url, token) else 1

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    interval = max(5.0, args.interval)
    last_ok = None
    while RUNNING:
        ok = run_once(args.hermes_url, args.bridge_url, token)
        if ok != last_ok:
            print("posted to bridge" if ok else "bridge post failed; retrying", flush=True)
            last_ok = ok
        deadline = time.monotonic() + interval
        while RUNNING and time.monotonic() < deadline:
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
