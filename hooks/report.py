#!/usr/bin/env python3
"""Convert a Claude or Codex lifecycle hook into a small local status file."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


CACHE_DIR = Path(
    os.environ.get(
        "AGENT_MONITOR_CACHE_DIR",
        str(Path.home() / ".cache/agent-monitor"),
    )
).expanduser()
PROVIDERS = {"claude": "Anthropic", "codex": "OpenAI"}


def read_input() -> dict:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_previous(agent: str) -> dict:
    try:
        value = json.loads((CACHE_DIR / f"{agent}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{path.stem}-",
        suffix=".json",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(payload, output, separators=(",", ":"), sort_keys=True)
            output.write("\n")
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def claude_usage(record: dict) -> tuple[dict, str | None]:
    message = record.get("message")
    if not isinstance(message, dict):
        return {}, None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = (
        safe_int(usage.get("input_tokens"))
        + safe_int(usage.get("cache_creation_input_tokens"))
        + safe_int(usage.get("cache_read_input_tokens"))
    )
    output_tokens = safe_int(usage.get("output_tokens"))
    model = message.get("model")
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total": input_tokens + output_tokens,
    }, str(model) if model else None


def codex_usage(record: dict) -> tuple[dict, str | None]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return {}, None
    if record.get("type") != "event_msg" or payload.get("type") != "token_count":
        return {}, None
    info = payload.get("info")
    if not isinstance(info, dict):
        return {}, None
    usage = info.get("total_token_usage")
    if not isinstance(usage, dict):
        return {}, None
    input_tokens = safe_int(usage.get("input_tokens"))
    output_tokens = safe_int(usage.get("output_tokens"))
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total": safe_int(usage.get("total_tokens")) or input_tokens + output_tokens,
    }, None


def latest_usage(agent: str, transcript_path: object) -> tuple[dict, str | None]:
    if not isinstance(transcript_path, str) or not transcript_path:
        return {}, None
    path = Path(transcript_path).expanduser()
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            source.seek(max(0, size - 524288))
            data = source.read().decode("utf-8", errors="ignore")
    except OSError:
        return {}, None

    parser = claude_usage if agent == "claude" else codex_usage
    for line in reversed(data.splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        usage, model = parser(record)
        if usage:
            return usage, model
    return {}, None


def normalized_status(agent: str, event: dict, previous: dict) -> dict:
    event_name = str(event.get("hook_event_name") or "Unknown")
    tool_name = str(event.get("tool_name") or "tool")
    model = event.get("model")
    usage, transcript_model = latest_usage(agent, event.get("transcript_path"))
    model_name = str(model or transcript_model or previous.get("model") or "waiting")
    tokens = usage or previous.get("tokens") or {"input": 0, "output": 0, "total": 0}
    tools = previous.get("tools")
    if not isinstance(tools, dict):
        tools = {"active": []}

    status = {
        "gateway": "online",
        "activity": "idle",
        "provider": PROVIDERS[agent],
        "model": model_name[:80],
        "latencyMs": None,
        "tokens": tokens,
        "tools": tools,
        "message": "Ready",
        "_event": event_name,
        "_session": str(event.get("session_id") or "")[:120],
    }

    if event_name == "SessionStart":
        status["message"] = "Session connected"
    elif event_name == "UserPromptSubmit":
        status["activity"] = "thinking"
        status["message"] = "Processing a request"
        status["tools"] = {"active": []}
    elif event_name == "PreToolUse":
        status["activity"] = "working"
        status["message"] = f"Using {tool_name}"[:160]
        status["tools"] = {"active": [tool_name[:40]]}
    elif event_name in ("PostToolUse", "PostToolUseFailure", "PostToolBatch"):
        status["activity"] = "working"
        status["message"] = f"Finished {tool_name}"[:160]
        status["tools"] = {"active": []}
    elif event_name in ("PermissionRequest", "Notification"):
        status["activity"] = "waiting"
        status["message"] = "Waiting for input"
    elif event_name in ("Stop", "TaskCompleted"):
        status["message"] = "Ready"
        status["tools"] = {"active": []}
    elif event_name in ("SessionEnd",):
        status["gateway"] = "offline"
        status["message"] = "Session ended"
        status["tools"] = {"active": []}
    else:
        status["activity"] = str(previous.get("activity") or "idle")
        status["message"] = str(previous.get("message") or "Ready")[:160]

    return status


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PROVIDERS:
        return 0
    agent = sys.argv[1]
    event = read_input()
    previous = load_previous(agent)
    write_atomic(CACHE_DIR / f"{agent}.json", normalized_status(agent, event, previous))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
