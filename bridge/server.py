#!/usr/bin/env python3
"""Tiny persistent status server for the Car Thing agent monitor."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = os.environ.get("AGENT_MONITOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("AGENT_MONITOR_PORT", "8892"))
TOKEN = os.environ.get("AGENT_MONITOR_TOKEN", "")
DATA_FILE = Path(
    os.environ.get(
        "AGENT_MONITOR_DATA_FILE",
        str(Path.home() / ".local/state/agent-monitor/status.json"),
    )
).expanduser()
AGENTS = ("hermes", "claude", "codex")
LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_status(agent: str) -> dict:
    provider = {
        "hermes": "Nous Research",
        "claude": "Anthropic",
        "codex": "OpenAI",
    }[agent]
    return {
        "gateway": "offline",
        "activity": "idle",
        "provider": provider,
        "model": "waiting",
        "latencyMs": None,
        "tokens": {"input": 0, "output": 0, "total": 0},
        "tools": {"active": []},
        "message": "Waiting for agent telemetry",
        "updatedAt": None,
    }


def load_state() -> dict:
    state = {agent: default_status(agent) for agent in AGENTS}
    try:
        stored = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return state
    if not isinstance(stored, dict):
        return state
    for agent in AGENTS:
        value = stored.get(agent)
        if isinstance(value, dict):
            state[agent].update(value)
    return state


STATE = load_state()


def save_state() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix="status-",
        suffix=".json",
        dir=str(DATA_FILE.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(STATE, output, separators=(",", ":"), sort_keys=True)
            output.write("\n")
        os.replace(temp_name, DATA_FILE)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def clean_status(agent: str, payload: dict) -> dict:
    current = default_status(agent)
    current.update(STATE.get(agent, {}))
    for key in (
        "gateway",
        "activity",
        "provider",
        "model",
        "latencyMs",
        "message",
    ):
        if key in payload:
            current[key] = payload[key]

    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        current["tokens"] = {
            "input": max(0, int(tokens.get("input") or 0)),
            "output": max(0, int(tokens.get("output") or 0)),
            "total": max(0, int(tokens.get("total") or 0)),
        }

    tools = payload.get("tools")
    if isinstance(tools, dict) and isinstance(tools.get("active"), list):
        current["tools"] = {
            "active": [str(tool)[:40] for tool in tools["active"][:6]]
        }

    current["message"] = str(current.get("message") or "")[:160]
    current["model"] = str(current.get("model") or "waiting")[:80]
    current["provider"] = str(current.get("provider") or "")[:80]
    current["updatedAt"] = now_iso()
    return current


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentMonitor/1"

    def log_message(self, message: str, *args: object) -> None:
        print(
            f"{self.log_date_time_string()} {self.client_address[0]} "
            f"{message % args}",
            flush=True,
        )

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_json(204, {})

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if path == "/health":
            self.send_json(200, {"ok": True, "service": "agent-monitor"})
            return
        if path.startswith("/status/"):
            agent = path.rsplit("/", 1)[-1]
            if agent not in AGENTS:
                self.send_json(404, {"error": "unknown agent"})
                return
            with LOCK:
                payload = dict(STATE[agent])
            self.send_json(200, payload)
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if not path.startswith("/status/"):
            self.send_json(404, {"error": "not found"})
            return
        agent = path.rsplit("/", 1)[-1]
        if agent not in AGENTS:
            self.send_json(404, {"error": "unknown agent"})
            return
        if TOKEN:
            supplied = self.headers.get("Authorization", "")
            if supplied != f"Bearer {TOKEN}":
                self.send_json(401, {"error": "unauthorized"})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 65536:
                raise ValueError("invalid body size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("body must be an object")
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid JSON"})
            return

        with LOCK:
            STATE[agent] = clean_status(agent, payload)
            save_state()
            response = dict(STATE[agent])
        self.send_json(200, response)


def main() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Agent monitor bridge listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
