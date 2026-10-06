#!/usr/bin/env python3
"""Post Hermes status to the Car Thing bridge.

Combines two content-free sources:
- Hermes's public liveness endpoint (GET /api/status): gateway state and active gateway chats
  (iMessage, the app, dashboard Bot Chat).
- Running `hermes [-p NAME] chat` processes on this machine (bot-to-bot messages, coding workers,
  one-shot runs). The gateway does not count these. Only process argument lists are read.

It does not read transcripts, and it reports no model or token counts because neither source has them.

Usage:
  AGENT_MONITOR_TOKEN_FILE=~/.local/state/agent-monitor/token python3 hooks/hermes_status.py
  python3 hooks/hermes_status.py --once        # single poll, exit 0 if posted
"""

from __future__ import annotations

import argparse
import json
import os
import re
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


def local_message(local: list[str]) -> str:
    if len(local) == 1:
        return f"{local[0]} working"
    return f"{len(local)} bots working: {', '.join(local)}"


def map_status(raw: dict | None, local: list[str] | tuple = ()) -> dict:
    """Combine gateway status (None when /api/status was unreachable) with local `hermes chat` runs."""
    local = list(local)
    tools = {"active": [name[:40] for name in local[:6]]}
    if raw is None or not raw.get("gateway_running"):
        if raw is None:
            idle_message = "Hermes status unavailable"
        else:
            idle_message = f"Gateway {str(raw.get('gateway_state') or 'stopped')[:40]}"
        return {"gateway": "offline", "activity": "working" if local else "idle", "provider": PROVIDER,
                "message": local_message(local) if local else idle_message, "tools": tools}
    try:
        agents = max(0, int(raw.get("active_agents") or 0))
    except (TypeError, ValueError):
        agents = 0
    gateway_busy = agents > 0 or bool(raw.get("gateway_busy"))
    if local and agents:
        message = f"{local_message(local)}, {agents} gateway chat{'s' if agents != 1 else ''}"
    elif local:
        message = local_message(local)
    elif gateway_busy and agents:
        message = f"{agents} agent{'s' if agents != 1 else ''} working"
    elif gateway_busy:
        message = "Working"
    else:
        message = "Ready"
    return {"gateway": "online", "activity": "working" if (gateway_busy or local) else "idle",
            "provider": PROVIDER, "message": message, "tools": tools}


def unavailable_status() -> dict:
    return map_status(None)


_PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _is_launcher(argv: list[str], i: int) -> bool:
    """`.../hermes` script path, or the bundled launcher's `python3 -I -c '<bootstrap>'` code."""
    arg = argv[i]
    if os.path.basename(arg) == "hermes":
        return True
    return i > 0 and argv[i - 1] == "-c" and "from hermes_cli.main import main" in arg


def profile_from_cmdline(argv: list[str]) -> str | None:
    """Profile of a `hermes [-p NAME|--profile NAME] chat ...` invocation, else None.

    Looks after the last launcher element, so a delivery wrapper whose argv embeds the child
    command resolves to the same profile as the child (deduplicated later).
    """
    starts = [i for i in range(len(argv)) if _is_launcher(argv, i)]
    if not starts:
        return None
    rest = argv[starts[-1] + 1:]
    profile = "default"
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("-p", "--profile") and i + 1 < len(rest):
            profile = rest[i + 1]
            i += 2
            continue
        if arg.startswith(("-p=", "--profile=")):
            profile = arg.split("=", 1)[1]
        elif arg == "chat":
            return profile if _PROFILE.match(profile) else None
        elif not arg.startswith("-"):
            return None  # another subcommand (gateway, dashboard, ...)
        i += 1
    return None


def profiles_from_cmdlines(cmdlines) -> list[str]:
    return sorted({name for argv in cmdlines if (name := profile_from_cmdline(argv))})


def local_bot_runs(proc_root: str = "/proc") -> list[str]:
    """Profiles with a running `hermes ... chat` process (bot-to-bot DMs, workers, one-shot runs).

    Reads only process argument lists, never chat content. These runs happen outside the gateway,
    so /api/status does not count them.
    """
    cmdlines = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            raw = Path(proc_root, entry, "cmdline").read_bytes()
        except OSError:
            continue
        cmdlines.append([part.decode("utf-8", "replace") for part in raw.split(b"\0") if part])
    return profiles_from_cmdlines(cmdlines)


def fetch_hermes(url: str) -> dict | None:
    """Raw /api/status payload, or None when Hermes's status endpoint is unreachable or malformed."""
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            raw = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


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


def run_once(hermes_url: str, bridge_url: str, token: str, proc_root: str = "/proc") -> bool:
    return post_status(bridge_url, token, map_status(fetch_hermes(hermes_url), local_bot_runs(proc_root)))


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
