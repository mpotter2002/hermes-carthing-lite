#!/usr/bin/env python3
"""Forward the latest local hook states to the Raspberry Pi bridge."""

from __future__ import annotations

import json
import os
import signal
import time
import urllib.error
import urllib.request
from pathlib import Path


CONFIG_FILE = Path(
    os.environ.get(
        "AGENT_MONITOR_CLIENT_CONFIG",
        str(Path.home() / ".config/agent-monitor/client.json"),
    )
).expanduser()
CACHE_DIR = Path(
    os.environ.get(
        "AGENT_MONITOR_CACHE_DIR",
        str(Path.home() / ".cache/agent-monitor"),
    )
).expanduser()
AGENTS = ("claude", "codex")
RUNNING = True


def stop(_signum: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def load_config() -> dict:
    try:
        value = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def read_status(agent: str) -> tuple[dict | None, int]:
    path = CACHE_DIR / f"{agent}.json"
    try:
        modified = path.stat().st_mtime_ns
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, 0
    if not isinstance(value, dict):
        return None, 0
    return {key: item for key, item in value.items() if not key.startswith("_")}, modified


def post_status(base_url: str, token: str, agent: str, status: dict) -> bool:
    body = json.dumps(status, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/status/{agent}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sent_versions = {agent: 0 for agent in AGENTS}
    last_heartbeat = {agent: 0.0 for agent in AGENTS}

    while RUNNING:
        config = load_config()
        base_url = str(config.get("baseUrl") or "").strip()
        token = str(config.get("token") or "")
        heartbeat_seconds = max(10, int(config.get("heartbeatSeconds") or 30))
        now = time.monotonic()

        if base_url:
            for agent in AGENTS:
                status, version = read_status(agent)
                if status is None:
                    continue
                changed = version != sent_versions[agent]
                heartbeat_due = now - last_heartbeat[agent] >= heartbeat_seconds
                if (changed or heartbeat_due) and post_status(
                    base_url, token, agent, status
                ):
                    sent_versions[agent] = version
                    last_heartbeat[agent] = now
        time.sleep(1)


if __name__ == "__main__":
    main()
