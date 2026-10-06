"""Stdlib tests for the loopback bridge and the Hermes status producer.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))

import hermes_status  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BridgeProcess:
    """Run bridge/server.py as a real subprocess with a temporary state dir."""

    def __init__(self, env_extra: dict):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "AGENT_MONITOR_PORT": str(self.port),
            "AGENT_MONITOR_DATA_FILE": str(Path(self.tmp.name) / "status.json"),
        }
        env.update(env_extra)
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bridge/server.py")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def wait_ready(self) -> None:
        # The bridge prints its bind address (flushed) before serving.
        self.first_line = self.proc.stdout.readline()
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"bridge exited: {self.proc.stdout.read()}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=0.5)
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("bridge did not start")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        self.proc.stdout.close()
        self.tmp.cleanup()


def token_file(directory: str, value: str = "test-token-123", mode: int = 0o600) -> str:
    path = Path(directory) / "token"
    path.write_text(value + "\n", encoding="utf-8")
    os.chmod(path, mode)
    return str(path)


def post(port: int, agent: str, body: dict, token: str | None) -> int:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/status/{agent}",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


class BridgeTests(unittest.TestCase):
    def test_refuses_to_start_without_token(self):
        bridge = BridgeProcess({})
        try:
            code = bridge.proc.wait(timeout=5)
            output = bridge.proc.stdout.read()
        finally:
            bridge.stop()
        self.assertNotEqual(code, 0)
        self.assertIn("token", output.lower())

    def test_refuses_group_or_world_readable_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = BridgeProcess({"AGENT_MONITOR_TOKEN_FILE": token_file(tmp, mode=0o644)})
            try:
                code = bridge.proc.wait(timeout=5)
                output = bridge.proc.stdout.read()
            finally:
                bridge.stop()
        self.assertNotEqual(code, 0)
        self.assertIn("0600", output)

    def test_defaults_to_loopback_and_authenticates_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = BridgeProcess({"AGENT_MONITOR_TOKEN_FILE": token_file(tmp)})
            try:
                bridge.wait_ready()
                self.assertIn("127.0.0.1:", bridge.first_line)
                self.assertNotIn("0.0.0.0", bridge.first_line)
                self.assertEqual(post(bridge.port, "hermes", {"gateway": "online"}, None), 401)
                self.assertEqual(post(bridge.port, "hermes", {"gateway": "online"}, "wrong"), 401)
                self.assertEqual(
                    post(bridge.port, "hermes", {"gateway": "online"}, "test-token-123"), 200
                )
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{bridge.port}/status/hermes", timeout=2
                ) as response:
                    self.assertEqual(json.load(response)["gateway"], "online")
            finally:
                bridge.stop()

    def test_env_token_still_supported(self):
        bridge = BridgeProcess({"AGENT_MONITOR_TOKEN": "env-token"})
        try:
            bridge.wait_ready()
            self.assertEqual(post(bridge.port, "claude", {"activity": "idle"}, "env-token"), 200)
        finally:
            bridge.stop()


class FakeHermes:
    """Serves a canned /api/status payload, or 500 when payload is None."""

    def __init__(self):
        self.payload: dict | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path != "/api/status" or owner.payload is None:
                    self.send_response(500)
                    self.end_headers()
                    return
                body = json.dumps(owner.payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/status"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class MapStatusTests(unittest.TestCase):
    def test_running_idle(self):
        status = hermes_status.map_status(
            {"gateway_running": True, "gateway_state": "running", "active_agents": 0,
             "gateway_busy": False, "version": "0.21.5"}
        )
        self.assertEqual(status["gateway"], "online")
        self.assertEqual(status["activity"], "idle")
        self.assertEqual(status["message"], "Ready")

    def test_busy_counts_agents(self):
        status = hermes_status.map_status(
            {"gateway_running": True, "active_agents": 2, "gateway_busy": True}
        )
        self.assertEqual(status["activity"], "working")
        self.assertEqual(status["message"], "2 agents working")
        one = hermes_status.map_status({"gateway_running": True, "active_agents": 1})
        self.assertEqual(one["message"], "1 agent working")

    def test_gateway_stopped(self):
        status = hermes_status.map_status({"gateway_running": False, "gateway_state": "stopped"})
        self.assertEqual(status["gateway"], "offline")
        self.assertEqual(status["activity"], "idle")
        self.assertIn("stopped", status["message"])

    def test_never_invents_model_or_tokens(self):
        status = hermes_status.map_status({"gateway_running": True, "active_agents": 0})
        self.assertNotIn("model", status)
        self.assertNotIn("tokens", status)

    def test_unavailable(self):
        status = hermes_status.unavailable_status()
        self.assertEqual(status["gateway"], "offline")


class ProducerEndToEndTests(unittest.TestCase):
    def test_once_posts_mapped_status_to_bridge(self):
        hermes = FakeHermes()
        with tempfile.TemporaryDirectory() as tmp:
            path = token_file(tmp)
            bridge = BridgeProcess({"AGENT_MONITOR_TOKEN_FILE": path})
            try:
                bridge.wait_ready()
                base = f"http://127.0.0.1:{bridge.port}"

                hermes.payload = {"gateway_running": True, "active_agents": 1, "gateway_busy": True}
                self.assertTrue(hermes_status.run_once(hermes.url, base, "test-token-123"))
                with urllib.request.urlopen(f"{base}/status/hermes", timeout=2) as response:
                    got = json.load(response)
                self.assertEqual(got["gateway"], "online")
                self.assertEqual(got["activity"], "working")

                hermes.payload = None  # Hermes status endpoint failing
                self.assertTrue(hermes_status.run_once(hermes.url, base, "test-token-123"))
                with urllib.request.urlopen(f"{base}/status/hermes", timeout=2) as response:
                    self.assertEqual(json.load(response)["gateway"], "offline")

                self.assertFalse(hermes_status.run_once(hermes.url, base, "wrong-token"))
            finally:
                bridge.stop()
                hermes.close()

    def test_producer_reads_token_file_and_rejects_loose_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(hermes_status.read_token(token_file(tmp)), "test-token-123")
            loose = token_file(tmp, mode=0o644)
            with self.assertRaises(SystemExit):
                hermes_status.read_token(loose)


if __name__ == "__main__":
    unittest.main()
