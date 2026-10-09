#!/usr/bin/env python3
"""Install or remove Claude/Codex telemetry hooks and the Mac forwarder."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path("/opt/homebrew/bin/python3")
REPORTER = PROJECT_ROOT / "hooks/report.py"
FORWARDER = PROJECT_ROOT / "hooks/forwarder.py"
CLAUDE_SETTINGS = Path.home() / ".claude/settings.json"
CODEX_HOOKS = Path.home() / ".codex/hooks.json"
CLIENT_CONFIG = Path.home() / ".config/agent-monitor/client.json"
LAUNCH_AGENT = (
    Path.home() / "Library/LaunchAgents/com.michaelpotter.agent-monitor.plist"
)
LAUNCH_LABEL = "com.michaelpotter.agent-monitor"
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "Stop",
    "SessionEnd",
)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, payload: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{path.name}-",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2)
            output.write("\n")
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def backup_once(path: Path) -> None:
    backup = path.with_name(f"{path.name}.agent-monitor-backup")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)


def command_for(agent: str) -> str:
    return f'"{PYTHON}" "{REPORTER}" {agent}'


def is_ours(entry: object, agent: str) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return False
    marker = f"{REPORTER}\" {agent}"
    return any(
        isinstance(hook, dict) and marker in str(hook.get("command") or "")
        for hook in hooks
    )


def update_hooks(path: Path, agent: str, remove: bool) -> None:
    settings = read_json(path)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{path}: hooks must be a JSON object")

    for event in EVENTS:
        entries = hooks.get(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"{path}: hooks.{event} must be a list")
        entries = [entry for entry in entries if not is_ours(entry, agent)]
        if not remove:
            entries.append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command_for(agent),
                            "timeout": 3,
                        }
                    ]
                }
            )
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)

    if not hooks:
        settings.pop("hooks", None)
    write_json(path, settings, 0o600)


def launch_agent_payload() -> dict:
    cache_dir = Path.home() / ".cache/agent-monitor"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": [str(PYTHON), str(FORWARDER)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(cache_dir / "forwarder.log"),
        "StandardErrorPath": str(cache_dir / "forwarder-error.log"),
    }


def install_launch_agent() -> None:
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    backup_once(LAUNCH_AGENT)
    with LAUNCH_AGENT.open("wb") as output:
        plistlib.dump(launch_agent_payload(), output)
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(LAUNCH_AGENT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        ["launchctl", "bootstrap", domain, str(LAUNCH_AGENT)],
        check=True,
    )


def remove_launch_agent() -> None:
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(LAUNCH_AGENT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        LAUNCH_AGENT.unlink()
    except FileNotFoundError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://hermes-pi.example:8892")
    parser.add_argument("--token")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()

    if not PYTHON.exists():
        raise SystemExit(f"Python was not found at {PYTHON}")

    backup_once(CLAUDE_SETTINGS)
    backup_once(CODEX_HOOKS)
    update_hooks(CLAUDE_SETTINGS, "claude", args.remove)
    update_hooks(CODEX_HOOKS, "codex", args.remove)

    if args.remove:
        remove_launch_agent()
        print("Removed agent monitor hooks and forwarder.")
        return 0

    if not args.token:
        raise SystemExit("--token is required when installing")
    write_json(
        CLIENT_CONFIG,
        {
            "baseUrl": args.base_url.rstrip("/"),
            "token": args.token,
            "heartbeatSeconds": 30,
        },
        0o600,
    )
    install_launch_agent()
    print(f"Installed hooks and forwarder for {args.base_url.rstrip('/')}.")
    print("Codex will load new hooks in newly started sessions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
