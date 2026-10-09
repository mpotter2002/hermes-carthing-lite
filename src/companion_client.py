#!/usr/bin/env python3
"""Read-only default Bot Chat companion projection.

This client uses Hermes's first-party native login, keeps credentials in memory,
observes exactly one owner-pinned runtime, and writes only a local snapshot file.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

VERSION = 1
CANONICAL_TITLE = "Bot Chat"
SENTINEL_SESSION_ID = "default:Bot Chat"
RPC_METHODS = frozenset({"session.events.since", "session.status"})
WS_PROTOCOL = "hermes-gateway-v1"
WS_TICKET_PREFIX = "hermes-gateway-ticket."
MAX_HTTP_BODY = 64 * 1024
MAX_WS_MESSAGE = 1024 * 1024
MAX_EVENTS = 512
MAX_REPLY_BYTES = 32 * 1024
POLL_SECONDS = 2.0
REQUEST_TIMEOUT = 8.0
CALLBACK_TIMEOUT = 180.0

TOOL_LABELS = {
    "browser_exec": "Browser",
    "delegate_task": "Worker",
    "execute_code": "Code",
    "patch": "Editing",
    "publish_html": "Publishing",
    "read_file": "Reading",
    "search_files": "Searching files",
    "terminal": "Terminal",
    "text_to_speech": "Speech",
    "vision_analyze": "Vision",
    "web_extract": "Reading web",
    "web_search": "Searching web",
    "write_file": "Writing",
}


class CompanionError(RuntimeError):
    """Expected failure whose message is safe to show without response bodies."""


class AuthRequired(CompanionError):
    pass


class StaleRuntime(CompanionError):
    pass


class ProtocolError(CompanionError):
    pass


class HttpFailure(CompanionError):
    def __init__(self, operation: str, status: int | None = None):
        self.status = status
        suffix = f" (HTTP {status})" if status is not None else ""
        super().__init__(f"{operation} failed{suffix}")


def epoch_ms() -> int:
    return time.time_ns() // 1_000_000


def unknown_snapshot(session_id: str = SENTINEL_SESSION_ID) -> dict[str, Any]:
    return {
        "version": VERSION,
        "profile": "default",
        "session_id": session_id,
        "observed_at": epoch_ms(),
        "activity": "unknown",
        "tool": None,
        "reply": None,
        "image": None,
        "cards": [],
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_locations(hermes_source: str, profile_home: str, output: str) -> tuple[Path, Path, Path]:
    source = Path(hermes_source).expanduser().resolve(strict=True)
    profile = Path(profile_home).expanduser().resolve(strict=True)
    target = Path(output).expanduser().resolve(strict=False)
    if not source.is_dir() or not (source / "hermes_state.py").is_file():
        raise CompanionError("--hermes-source is not a Hermes source checkout")
    if not profile.is_dir():
        raise CompanionError("--profile-home must be an existing default profile home")
    if profile.parent.name == "profiles":
        raise CompanionError("named profiles are not supported; --profile-home must be the default profile")
    if target == profile or target == source or _inside(target, profile) or _inside(target, source):
        raise CompanionError("--output must be outside Hermes source and profile paths")
    return source, profile, target


def validate_origin(raw: str) -> str:
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CompanionError("--origin must be an absolute HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CompanionError("--origin cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise CompanionError("--origin cannot contain a path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CompanionError("--origin has an invalid port") from exc
    host = parsed.hostname
    if parsed.scheme == "http":
        allowed = host == "localhost"
        try:
            address = ipaddress.ip_address(host)
            carrier_grade_nat = address in ipaddress.ip_network("100.64.0.0/10")
            allowed = address.is_loopback or address.is_private or address.is_link_local or carrier_grade_nat
        except ValueError:
            pass
        if not allowed:
            raise CompanionError("plain HTTP is allowed only for an explicit local/private gateway")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    return f"{parsed.scheme}://{authority}"


class SnapshotWriter:
    def __init__(self, output: Path):
        self.output = output

    def write(self, snapshot: dict[str, Any]) -> None:
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        self.output.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.output.with_name(f".{self.output.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.output)
            try:
                directory_fd = os.open(self.output.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def truncate_utf8(text: str, limit: int = MAX_REPLY_BYTES) -> str:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def redact_reply(text: str, redactor: Callable[..., str]) -> str | None:
    """Use Hermes's supported persisted-text redactor; fail closed if it fails."""
    try:
        clean = redactor(str(text), force=True)
    except Exception:
        return None
    if not isinstance(clean, str):
        return None
    return truncate_utf8(clean)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str, str]:
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(32))
    return verifier, challenge, state


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_json(
    url: str,
    *,
    operation: str,
    method: str = "POST",
    body: dict[str, Any] | None = None,
    bearer: str | None = None,
    timeout: float = REQUEST_TIMEOUT,
    opener=None,
) -> dict[str, Any]:
    data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = opener or urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.geturl() != url:
                raise HttpFailure(operation)
            raw = response.read(MAX_HTTP_BODY + 1)
            if len(raw) > MAX_HTTP_BODY:
                raise HttpFailure(operation)
    except urllib.error.HTTPError as exc:
        raise HttpFailure(operation, exc.code) from None
    except (OSError, ValueError):
        raise HttpFailure(operation) from None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HttpFailure(operation) from None
    if not isinstance(value, dict):
        raise HttpFailure(operation)
    return value


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float
    provider: str
    user_id: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Tokens":
        fields = ("access_token", "refresh_token", "expires_at", "provider", "user_id")
        if payload.get("token_type") != "Bearer" or any(key not in payload for key in fields):
            raise AuthRequired("login response was invalid; sign in again")
        access = payload["access_token"]
        refresh = payload["refresh_token"]
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise AuthRequired("login response was invalid; sign in again")
        try:
            expires = float(payload["expires_at"])
        except (TypeError, ValueError):
            raise AuthRequired("login response was invalid; sign in again") from None
        return cls(access, refresh, expires, str(payload["provider"]), str(payload["user_id"]))

    def clear(self) -> None:
        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0
        self.provider = ""
        self.user_id = ""


class CallbackState:
    def __init__(self, path: str, state: str):
        self.path = path
        self.state = state
        self.used = False

    def accept(self, target: str) -> str:
        parsed = urllib.parse.urlsplit(target)
        if self.used:
            raise AuthRequired("duplicate login callback denied")
        if parsed.path != self.path or parsed.fragment:
            raise AuthRequired("unexpected login callback denied")
        values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if set(values) != {"code", "state"} or len(values["code"]) != 1 or len(values["state"]) != 1:
            raise AuthRequired("invalid login callback denied")
        if not secrets.compare_digest(values["state"][0], self.state):
            raise AuthRequired("login state mismatch denied")
        code = values["code"][0]
        if not code:
            raise AuthRequired("empty login code denied")
        self.used = True
        return code


class NativeLogin:
    def __init__(self, origin: str, provider: str = "", timeout: float = CALLBACK_TIMEOUT):
        self.origin = validate_origin(origin)
        self.provider = provider
        self.timeout = min(max(float(timeout), 0.1), CALLBACK_TIMEOUT)

    def login(self, announce: Callable[[str], None] = print) -> Tokens:
        verifier, challenge, state = make_pkce()
        path = f"/hermes-companion-{secrets.token_hex(16)}"
        capture = CallbackState(path, state)
        result: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                try:
                    result["code"] = capture.accept(self.path)
                    status, message = 200, b"Hermes companion sign-in complete. You may close this tab."
                except (AuthRequired, ValueError):
                    result["error"] = "callback denied"
                    status, message = 400, b"Sign-in callback denied. Return to the companion and retry."
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(message)

        server = HTTPServer(("127.0.0.1", 0), Handler, bind_and_activate=True)
        server.timeout = min(0.25, self.timeout)
        redirect_uri = f"http://127.0.0.1:{server.server_port}{path}"
        query = {
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if self.provider:
            query["provider"] = self.provider
        authorize_url = self.origin + "/auth/native/authorize?" + urllib.parse.urlencode(query)
        announce(authorize_url)
        deadline = time.monotonic() + self.timeout
        try:
            while time.monotonic() < deadline and not result:
                server.handle_request()
        finally:
            server.server_close()
        if "code" not in result:
            if result:
                raise AuthRequired("login callback denied; sign in again")
            raise AuthRequired("login callback timed out; sign in again")
        payload = _http_json(
            self.origin + "/auth/native/token",
            operation="native token exchange",
            body={"code": result["code"], "code_verifier": verifier},
        )
        return Tokens.from_payload(payload)


class AuthSession:
    def __init__(self, origin: str, tokens: Tokens):
        self.origin = validate_origin(origin)
        self.tokens = tokens

    def _refresh(self) -> None:
        old_refresh = self.tokens.refresh_token
        old_provider = self.tokens.provider
        try:
            payload = _http_json(
                self.origin + "/auth/native/refresh",
                operation="native token refresh",
                body={"refresh_token": old_refresh, "provider": old_provider},
            )
            self.tokens = Tokens.from_payload(payload)
        except (HttpFailure, AuthRequired):
            self.tokens.clear()
            raise AuthRequired("authentication expired; run an explicit new login") from None

    def ticket(self) -> str:
        if not self.tokens.access_token:
            raise AuthRequired("authentication required")
        if self.tokens.expires_at and time.time() >= self.tokens.expires_at - 15:
            self._refresh()
        try:
            payload = _http_json(
                self.origin + "/api/auth/ws-ticket",
                operation="WebSocket ticket request",
                body=None,
                bearer=self.tokens.access_token,
            )
        except HttpFailure as exc:
            if exc.status == 401 and self.tokens.refresh_token:
                self._refresh()
                try:
                    payload = _http_json(
                        self.origin + "/api/auth/ws-ticket",
                        operation="WebSocket ticket request",
                        body=None,
                        bearer=self.tokens.access_token,
                    )
                except HttpFailure:
                    self.tokens.clear()
                    raise AuthRequired("authentication failed; run an explicit new login") from None
            else:
                if exc.status in {401, 403}:
                    self.tokens.clear()
                    raise AuthRequired("authentication failed; run an explicit new login") from None
                raise
        ticket = payload.get("ticket")
        if not isinstance(ticket, str) or not ticket or any(ch in ticket for ch in "\r\n, "):
            self.tokens.clear()
            raise AuthRequired("ticket response was invalid; run an explicit new login")
        return ticket


@dataclass(frozen=True)
class OwnerPin:
    durable_id: str
    runtime_id: str
    lease_id: str
    pid: int
    process_start_time: float


def linux_process_start(pid: int) -> float | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        # Everything before the final ')' is pid/comm; starttime is field 22.
        fields = stat[stat.rfind(")") + 2 :].split()
        ticks = int(fields[19])
        boot = None
        with open("/proc/stat", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("btime "):
                    boot = int(line.split()[1])
                    break
        if boot is None:
            return None
        return boot + ticks / os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    except (OSError, ValueError, IndexError):
        return None


def process_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    values = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    return values or None


def command_is_hermes(cmdline: list[str] | None, source: Path) -> bool:
    if not cmdline:
        return False
    joined = "\n".join(cmdline)
    source_text = str(source.resolve())
    has_source = source_text in joined or any(
        value.startswith(source_text + os.sep) for value in cmdline
    )
    has_app = bool(re.search(r"(^|[/.])hermes(?:_cli|-agent)?([/.\s]|$)|tui_gateway", joined))
    return has_source and has_app


def read_registry(path: Path, retries: int = 2) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            if path.stat().st_size > MAX_WS_MESSAGE:
                raise CompanionError("active-session registry is too large")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            entries = data.get("entries") if isinstance(data, dict) else data
            if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
                raise CompanionError("active-session registry has invalid shape")
            return entries
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
    raise CompanionError("active-session registry is unreadable") from last_error


def resolve_owner(
    entries: list[dict[str, Any]],
    durable_id: str,
    source: Path,
    *,
    start_probe: Callable[[int], float | None] = linux_process_start,
    cmdline_probe: Callable[[int], list[str] | None] = process_cmdline,
) -> OwnerPin | None:
    matches = [entry for entry in entries if entry.get("session_id") == durable_id]
    if not matches:
        return None
    if len(matches) != 1:
        raise CompanionError("canonical Bot Chat has ambiguous live ownership")
    entry = matches[0]
    if entry.get("surface") != "desktop":
        raise CompanionError("canonical owner is not the desktop Bot Chat surface")
    metadata = entry.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("bot_live_delivery_consumer") is not True:
        raise CompanionError("canonical owner is not an advertised Bot Chat consumer")
    runtime = metadata.get("live_session_id")
    lease = entry.get("lease_id")
    expected_start = entry.get("process_start_time")
    pid = entry.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise CompanionError("canonical owner PID is invalid")
    if not isinstance(runtime, str) or not runtime or not isinstance(lease, str) or not lease:
        raise CompanionError("canonical owner identity is incomplete")
    try:
        expected = float(expected_start)
    except (TypeError, ValueError):
        raise CompanionError("canonical owner process identity is missing") from None
    current = start_probe(pid)
    if current is None or abs(current - expected) >= 0.001:
        raise CompanionError("canonical owner PID is dead or recycled")
    if not command_is_hermes(cmdline_probe(pid), source):
        raise CompanionError("canonical owner is not the configured Hermes application")
    return OwnerPin(durable_id, runtime, lease, pid, expected)


class HermesStore:
    def __init__(self, source: Path, profile_home: Path):
        self.source = source
        self.profile_home = profile_home
        source_text = str(source)
        if source_text not in sys.path:
            sys.path.insert(0, source_text)
        try:
            from hermes_state import SessionDB
            from agent.redact import redact_sensitive_text
        except Exception as exc:
            raise CompanionError("configured Hermes source cannot provide SessionDB/redaction") from exc
        self._db_type = SessionDB
        self._redactor = redact_sensitive_text

    def canonical_id(self) -> str | None:
        db = self._db_type(db_path=self.profile_home / "state.db", read_only=True)
        try:
            row = db.get_session_by_title(CANONICAL_TITLE)
            tip = db.get_compression_tip(row["id"]) if row else None
            return str(tip) if tip else None
        finally:
            db.close()

    def latest_reply(self, durable_id: str, wanted_row_id: int | None = None) -> dict[str, str] | None:
        db = self._db_type(db_path=self.profile_home / "state.db", read_only=True)
        try:
            rows = db.get_messages_as_conversation(
                durable_id,
                include_ancestors=True,
                include_compacted=True,
                include_row_ids=True,
            )
        finally:
            db.close()
        candidates = [
            row for row in rows
            if row.get("role") == "assistant" and not row.get("tool_calls")
            and isinstance(row.get("content"), str) and isinstance(row.get("_row_id"), int)
        ]
        if wanted_row_id is not None:
            candidates = [row for row in candidates if row["_row_id"] == wanted_row_id]
        if not candidates:
            return None
        row = candidates[-1]
        text = redact_reply(row["content"], self._redactor)
        if text is None:
            return None
        return {"id": str(row["_row_id"]), "text": text}


@dataclass
class ProjectionState:
    durable_id: str
    runtime_id: str
    epoch: str | None = None
    last_seq: int = 0
    turn_active: bool = False
    thinking: bool = False
    tools: dict[str, str | None] = field(default_factory=dict)
    reply: dict[str, str] | None = None
    wanted_row_id: int | None = None

    def clear_live(self) -> None:
        self.last_seq = 0
        self.turn_active = False
        self.thinking = False
        self.tools.clear()
        self.wanted_row_id = None

    def disconnect(self) -> None:
        self.clear_live()

    def _apply_event(self, event: dict[str, Any]) -> None:
        if event.get("session_id") != self.runtime_id:
            raise ProtocolError("cross-session event denied")
        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(event_type, str) or not isinstance(payload, dict):
            raise ProtocolError("invalid event shape")
        if event_type == "message.start":
            self.turn_active = True
            self.thinking = False
            self.tools.clear()
        elif event_type in {"thinking.delta", "reasoning.delta"}:
            if self.turn_active:
                self.thinking = True
        elif event_type == "message.delta":
            if self.turn_active:
                self.thinking = False
        elif event_type == "tool.start":
            tool_id, name = payload.get("tool_id"), payload.get("name")
            if isinstance(tool_id, str) and tool_id and len(tool_id) <= 256:
                self.tools[tool_id] = TOOL_LABELS.get(name) if isinstance(name, str) else None
                self.thinking = False
        elif event_type == "tool.complete":
            tool_id = payload.get("tool_id")
            if isinstance(tool_id, str):
                self.tools.pop(tool_id, None)
        elif event_type == "message.complete":
            self.turn_active = False
            self.thinking = False
            self.tools.clear()
            persisted = payload.get("persisted_turn")
            row_id = persisted.get("final_assistant_row_id") if isinstance(persisted, dict) else None
            self.wanted_row_id = row_id if isinstance(row_id, int) and not isinstance(row_id, bool) else None

    def apply_replay(self, result: dict[str, Any]) -> bool:
        events = result.get("events")
        epoch = result.get("epoch")
        latest = result.get("latest_seq")
        truncated = result.get("truncated")
        if (
            not isinstance(events, list) or len(events) > MAX_EVENTS
            or not isinstance(epoch, str) or not epoch
            or not isinstance(latest, int) or isinstance(latest, bool) or latest < 0
            or not isinstance(truncated, bool)
        ):
            raise ProtocolError("invalid replay response")
        reset = False
        if self.epoch is not None and epoch != self.epoch:
            self.clear_live()
            reset = True
        self.epoch = epoch
        if truncated:
            self.turn_active = False
            self.thinking = False
            self.tools.clear()
            reset = True
        accepted = self.last_seq
        for event in events:
            if not isinstance(event, dict):
                raise ProtocolError("invalid replay event")
            seq = event.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq <= 0:
                raise ProtocolError("invalid event sequence")
            if seq <= accepted:
                continue
            if seq != accepted + 1:
                self.turn_active = False
                self.thinking = False
                self.tools.clear()
                reset = True
            self._apply_event(event)
            accepted = seq
        if latest < accepted:
            raise ProtocolError("replay watermark moved backwards")
        self.last_seq = latest
        return reset

    def apply_status(self, output: str) -> bool | None:
        if not isinstance(output, str) or len(output.encode("utf-8", "replace")) > 64 * 1024:
            return None
        values = re.findall(r"(?m)^Agent Running: (Yes|No)$", output)
        if len(values) != 1:
            return None
        running = values[0] == "Yes"
        if not running:
            self.turn_active = False
            self.thinking = False
            self.tools.clear()
        return running

    def snapshot(self, running: bool | None) -> dict[str, Any]:
        if running is None:
            activity = "unknown"
            tool = None
        elif not running:
            activity = "idle"
            tool = None
        else:
            labels = [label for label in self.tools.values() if label]
            tool = labels[-1] if labels else None
            activity = "running" if self.tools or not self.thinking else "thinking"
        return {
            "version": VERSION,
            "profile": "default",
            "session_id": self.durable_id,
            "observed_at": epoch_ms(),
            "activity": activity,
            "tool": tool,
            "reply": self.reply,
            "image": None,
            "cards": [],
        }


class RpcClient:
    def __init__(self, websocket, runtime_id: str):
        self.websocket = websocket
        self.runtime_id = runtime_id
        self.next_id = 1

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method not in RPC_METHODS:
            raise ProtocolError("RPC method is not allowlisted")
        if params.get("session_id") != self.runtime_id:
            raise ProtocolError("RPC session is not the pinned runtime")
        allowed = {"session_id", "last_seen"} if method == "session.events.since" else {"session_id"}
        if set(params) != allowed:
            raise ProtocolError("RPC parameters are not allowlisted")
        request_id = self.next_id
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        await asyncio.wait_for(self.websocket.send(json.dumps(request, separators=(",", ":"))), REQUEST_TIMEOUT)
        while True:
            raw = await asyncio.wait_for(self.websocket.recv(), REQUEST_TIMEOUT)
            if not isinstance(raw, str) or len(raw.encode("utf-8", "replace")) > MAX_WS_MESSAGE:
                raise ProtocolError("invalid WebSocket message")
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                raise ProtocolError("invalid WebSocket JSON") from None
            if not isinstance(response, dict):
                raise ProtocolError("invalid RPC response")
            if response.get("id") != request_id:
                if response.get("method") == "event":
                    continue
                raise ProtocolError("unexpected RPC response id")
            error = response.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                if code == 4001:
                    raise StaleRuntime("pinned runtime is stale")
                raise ProtocolError(f"RPC failed ({code})")
            result = response.get("result")
            if not isinstance(result, dict):
                raise ProtocolError("RPC result is invalid")
            return result


def _owner_unchanged(left: OwnerPin, right: OwnerPin | None) -> bool:
    return right is not None and left == right


class CompanionClient:
    def __init__(self, source: Path, profile: Path, origin: str, output: Path, auth: AuthSession):
        self.source = source
        self.profile = profile
        self.origin = validate_origin(origin)
        self.writer = SnapshotWriter(output)
        self.auth = auth
        self.store = HermesStore(source, profile)
        self.registry = profile / "runtime" / "active_sessions.json"

    def discover(self) -> tuple[str | None, OwnerPin | None]:
        durable = self.store.canonical_id()
        if durable is None:
            return None, None
        return durable, resolve_owner(read_registry(self.registry), durable, self.source)

    def _ws_url(self) -> str:
        scheme = "wss" if self.origin.startswith("https://") else "ws"
        return scheme + self.origin.split(":", 1)[1] + "/api/ws"

    async def _observe(self, owner: OwnerPin, deadline: float) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise CompanionError("runtime requires the optional websockets package") from exc
        ticket = self.auth.ticket()
        protocols = [WS_PROTOCOL, WS_TICKET_PREFIX + ticket]
        async with websockets.connect(
            self._ws_url(),
            subprotocols=protocols,
            open_timeout=REQUEST_TIMEOUT,
            close_timeout=REQUEST_TIMEOUT,
            max_size=MAX_WS_MESSAGE,
            ping_interval=20,
            ping_timeout=REQUEST_TIMEOUT,
        ) as websocket:
            if websocket.subprotocol != WS_PROTOCOL:
                raise ProtocolError("gateway did not select the pinned public subprotocol")
            raw = await asyncio.wait_for(websocket.recv(), REQUEST_TIMEOUT)
            ready = json.loads(raw)
            params = ready.get("params") if isinstance(ready, dict) else None
            payload = params.get("payload") if isinstance(params, dict) else None
            if not (
                ready.get("method") == "event" and params.get("type") == "gateway.ready"
                and isinstance(payload, dict) and isinstance(payload.get("replay_epoch"), str)
            ):
                raise ProtocolError("origin is not the expected Hermes gateway application")
            state = ProjectionState(owner.durable_id, owner.runtime_id, epoch=payload["replay_epoch"])
            state.reply = self.store.latest_reply(owner.durable_id)
            rpc = RpcClient(websocket, owner.runtime_id)
            while time.monotonic() < deadline:
                current_durable, current_owner = self.discover()
                if current_durable != owner.durable_id or not _owner_unchanged(owner, current_owner):
                    state.disconnect()
                    self.writer.write(state.snapshot(None))
                    return
                replay = await rpc.call(
                    "session.events.since",
                    {"session_id": owner.runtime_id, "last_seen": state.last_seq},
                )
                reset = state.apply_replay(replay)
                status = await rpc.call("session.status", {"session_id": owner.runtime_id})
                output = status.get("output")
                running = state.apply_status(output) if isinstance(output, str) else None
                if reset or state.wanted_row_id is not None:
                    reconciled = self.store.latest_reply(owner.durable_id, state.wanted_row_id)
                    if reconciled is not None:
                        state.reply = reconciled
                        state.wanted_row_id = None
                    elif reset:
                        state.reply = self.store.latest_reply(owner.durable_id)
                self.writer.write(state.snapshot(running))
                await asyncio.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))

    async def run(self, duration: float) -> None:
        deadline = time.monotonic() + duration
        self.writer.write(unknown_snapshot())
        backoff = 0.5
        while time.monotonic() < deadline:
            try:
                durable, owner = self.discover()
                if durable is None:
                    self.writer.write(unknown_snapshot())
                elif owner is None:
                    self.writer.write(unknown_snapshot(durable))
                else:
                    await self._observe(owner, deadline)
                    backoff = 0.5
                    continue
            except AuthRequired:
                raise
            except (CompanionError, OSError, asyncio.TimeoutError):
                # A disconnected projection retains only the last committed DB reply when available.
                durable = None
                try:
                    durable = self.store.canonical_id()
                    reply = self.store.latest_reply(durable) if durable else None
                except Exception:
                    reply = None
                snapshot = unknown_snapshot(durable or SENTINEL_SESSION_ID)
                snapshot["reply"] = reply
                self.writer.write(snapshot)
            delay = min(backoff, max(0.0, deadline - time.monotonic()))
            if delay:
                await asyncio.sleep(delay)
            backoff = min(backoff * 2, 8.0)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only default Bot Chat companion client")
    parser.add_argument("--hermes-source", required=True)
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", default="")
    parser.add_argument("--duration", type=float, default=3600.0)
    args = parser.parse_args(argv)
    if not 0 < args.duration <= 24 * 60 * 60:
        parser.error("--duration must be greater than zero and no more than 86400 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source, profile, output = validate_locations(args.hermes_source, args.profile_home, args.output)
        origin = validate_origin(args.origin)
        SnapshotWriter(output).write(unknown_snapshot())
        tokens = NativeLogin(origin, args.provider).login(
            lambda url: print(f"Open this authorization URL in your browser:\n{url}", flush=True)
        )
        auth = AuthSession(origin, tokens)
        asyncio.run(CompanionClient(source, profile, origin, output, auth).run(args.duration))
        return 0
    except KeyboardInterrupt:
        return 130
    except CompanionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
