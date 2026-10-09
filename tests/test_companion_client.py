"""Offline stdlib tests for the V1 companion client.

All protocol payloads here are local fixtures, not live Hermes evidence.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import companion_client as cc  # noqa: E402


def event(seq: int, event_type: str, payload: dict | None = None, session: str = "runtime") -> dict:
    return {"type": event_type, "session_id": session, "payload": payload or {}, "seq": seq}


def replay(events: list[dict], *, latest: int | None = None, epoch: str = "epoch-1", truncated=False):
    return {
        "events": events,
        "latest_seq": latest if latest is not None else (events[-1]["seq"] if events else 0),
        "truncated": truncated,
        "count": len(events),
        "epoch": epoch,
        "open_requests": [],
    }


class AuthTests(unittest.TestCase):
    def test_pkce_and_callback_state_duplicate_and_wrong_path(self):
        verifier, challenge, state = cc.make_pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertNotIn("=", verifier)
        self.assertEqual(challenge, cc._b64url(cc.hashlib.sha256(verifier.encode("ascii")).digest()))
        callback = cc.CallbackState("/callback", state)
        with self.assertRaises(cc.AuthRequired):
            callback.accept("/wrong?code=x&state=" + state)
        self.assertEqual(callback.accept("/callback?code=code-1&state=" + state), "code-1")
        with self.assertRaises(cc.AuthRequired):
            callback.accept("/callback?code=code-2&state=" + state)

    def test_state_mismatch_has_no_token_exchange(self):
        callback = cc.CallbackState("/callback", "expected")
        with self.assertRaisesRegex(cc.AuthRequired, "state mismatch"):
            callback.accept("/callback?code=secret-code&state=wrong")
        self.assertFalse(callback.used)

    def test_loopback_native_login_and_exact_token_body(self):
        captured = {}

        def announce(url: str):
            parsed = urllib.parse.urlsplit(url)
            values = urllib.parse.parse_qs(parsed.query)
            self.assertEqual(values["code_challenge_method"], ["S256"])
            redirect = values["redirect_uri"][0]
            state = values["state"][0]

            def callback():
                with urllib.request.urlopen(
                    redirect + "?" + urllib.parse.urlencode({"code": "local-fixture-code", "state": state}),
                    timeout=2,
                ) as response:
                    response.read()

            threading.Thread(target=callback, daemon=True).start()

        def fake_http(url, **kwargs):
            captured["url"] = url
            captured["body"] = kwargs["body"]
            return {
                "access_token": "access-fixture",
                "refresh_token": "refresh-fixture",
                "token_type": "Bearer",
                "expires_at": time.time() + 100,
                "provider": "fixture",
                "user_id": "user-fixture",
            }

        with mock.patch.object(cc, "_http_json", side_effect=fake_http):
            tokens = cc.NativeLogin("http://127.0.0.1:9118", timeout=2).login(announce)
        self.assertEqual(tokens.access_token, "access-fixture")
        self.assertEqual(captured["url"], "http://127.0.0.1:9118/auth/native/token")
        self.assertEqual(captured["body"]["code"], "local-fixture-code")
        self.assertEqual(set(captured["body"]), {"code", "code_verifier"})

    def test_callback_deadline_denied_without_exchange(self):
        exchange = mock.Mock()
        with mock.patch.object(cc, "_http_json", exchange):
            with self.assertRaisesRegex(cc.AuthRequired, "timed out"):
                cc.NativeLogin("http://127.0.0.1:9118", timeout=0.1).login(lambda _url: None)
        exchange.assert_not_called()

    def test_token_payload_exact_fields_and_clear(self):
        payload = {
            "access_token": "a", "refresh_token": "r", "token_type": "Bearer",
            "expires_at": 123, "provider": "p", "user_id": "u",
        }
        tokens = cc.Tokens.from_payload(payload)
        self.assertEqual((tokens.provider, tokens.user_id), ("p", "u"))
        tokens.clear()
        self.assertEqual((tokens.access_token, tokens.refresh_token), ("", ""))
        with self.assertRaises(cc.AuthRequired):
            cc.Tokens.from_payload({**payload, "token_type": "Basic"})

    def test_refresh_rotates_tokens_and_stays_origin_bound(self):
        old = cc.Tokens("old-a", "old-r", time.time() - 1, "p", "u")
        calls = []

        def fixture(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/auth/native/refresh"):
                return {
                    "access_token": "new-a", "refresh_token": "new-r", "token_type": "Bearer",
                    "expires_at": time.time() + 60, "provider": "p", "user_id": "u",
                }
            return {"ticket": "ticket-fixture", "ttl_seconds": 30}

        with mock.patch.object(cc, "_http_json", side_effect=fixture):
            auth = cc.AuthSession("https://gateway.example", old)
            self.assertEqual(auth.ticket(), "ticket-fixture")
        self.assertEqual([url for url, _ in calls], [
            "https://gateway.example/auth/native/refresh",
            "https://gateway.example/api/auth/ws-ticket",
        ])
        self.assertEqual(auth.tokens.refresh_token, "new-r")

    def test_auth_failure_clears_tokens_without_leaking_body(self):
        secret = "super-secret-response-token"
        error = urllib.error.HTTPError(
            "https://gateway.example/api/auth/ws-ticket", 401, secret, {}, None
        )

        class Opener:
            def open(self, request, timeout):
                raise error

        with self.assertRaises(cc.HttpFailure) as caught:
            cc._http_json(
                "https://gateway.example/api/auth/ws-ticket",
                operation="ticket",
                bearer="access-secret",
                opener=Opener(),
            )
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("access-secret", str(caught.exception))

        tokens = cc.Tokens("a", "r", time.time() + 60, "p", "u")
        with mock.patch.object(cc, "_http_json", side_effect=cc.HttpFailure("ticket", 403)):
            with self.assertRaises(cc.AuthRequired):
                cc.AuthSession("https://gateway.example", tokens).ticket()
        self.assertEqual(tokens.access_token, "")

    def test_redirect_and_origin_changes_refused(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return "https://evil.example/token"
            def read(self, _limit): return b'{"ticket":"x"}'

        class Opener:
            def open(self, request, timeout): return Response()

        with self.assertRaises(cc.HttpFailure):
            cc._http_json("https://gateway.example/token", operation="token", opener=Opener())
        self.assertEqual(cc.validate_origin("http://127.0.0.1:9118"), "http://127.0.0.1:9118")
        self.assertEqual(cc.validate_origin("http://100.87.1.2:9118"), "http://100.87.1.2:9118")
        for invalid in (
            "http://example.com", "https://u:p@example.com", "https://example.com/path",
            "https://example.com?token=x", "ftp://127.0.0.1",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(cc.CompanionError):
                cc.validate_origin(invalid)


class RpcTests(unittest.IsolatedAsyncioTestCase):
    class WS:
        def __init__(self, replies):
            self.replies = list(replies)
            self.sent = []

        async def send(self, value): self.sent.append(json.loads(value))
        async def recv(self): return json.dumps(self.replies.pop(0))

    async def test_closed_allowlist_and_pinned_runtime(self):
        ws = self.WS([{"jsonrpc": "2.0", "id": 1, "result": {"output": "ok"}}])
        rpc = cc.RpcClient(ws, "runtime")
        result = await rpc.call("session.status", {"session_id": "runtime"})
        self.assertEqual(result, {"output": "ok"})
        self.assertEqual(ws.sent[0]["method"], "session.status")
        for method in ("session.history", "session.resume", "session.activate", "session.input"):
            with self.subTest(method=method), self.assertRaises(cc.ProtocolError):
                await rpc.call(method, {"session_id": "runtime"})
        with self.assertRaises(cc.ProtocolError):
            await rpc.call("session.status", {"session_id": "other"})
        with self.assertRaises(cc.ProtocolError):
            await rpc.call("session.status", {"session_id": "runtime", "extra": True})

    async def test_stale_4001_means_rediscover(self):
        ws = self.WS([{"jsonrpc": "2.0", "id": 1, "error": {"code": 4001, "message": "fixture"}}])
        with self.assertRaises(cc.StaleRuntime):
            await cc.RpcClient(ws, "runtime").call("session.status", {"session_id": "runtime"})


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name, "hermes-agent")
        self.source.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def owner(self, **changes):
        entry = {
            "session_id": "durable", "lease_id": "lease", "pid": 42,
            "process_start_time": 100.5, "surface": "desktop",
            "metadata": {"bot_live_delivery_consumer": True, "live_session_id": "runtime"},
        }
        entry.update(changes)
        return entry

    def resolve(self, entries):
        return cc.resolve_owner(
            entries, "durable", self.source,
            start_probe=lambda pid: 100.5,
            cmdline_probe=lambda pid: ["python", str(self.source / "hermes_cli/main.py"), "dashboard"],
        )

    def test_exact_metadata_pid_session_and_process_identity(self):
        pin = self.resolve([self.owner()])
        self.assertEqual(pin, cc.OwnerPin("durable", "runtime", "lease", 42, 100.5))
        self.assertIsNone(self.resolve([self.owner(session_id="other")]))
        for broken in (
            self.owner(surface="tui"),
            self.owner(metadata={"live_session_id": "runtime"}),
            self.owner(metadata={"bot_live_delivery_consumer": True, "live_session_id": ""}),
            self.owner(pid=True),
            self.owner(process_start_time=None),
        ):
            with self.subTest(broken=broken), self.assertRaises(cc.CompanionError):
                self.resolve([broken])
        with self.assertRaises(cc.CompanionError):
            self.resolve([self.owner(), self.owner(lease_id="other")])

    def test_recycled_pid_and_wrong_serving_application_fail_closed(self):
        with self.assertRaisesRegex(cc.CompanionError, "recycled"):
            cc.resolve_owner(
                [self.owner()], "durable", self.source,
                start_probe=lambda pid: 101.0,
                cmdline_probe=lambda pid: [str(self.source / "hermes_cli/main.py")],
            )
        with self.assertRaisesRegex(cc.CompanionError, "configured Hermes"):
            cc.resolve_owner(
                [self.owner()], "durable", self.source,
                start_probe=lambda pid: 100.5,
                cmdline_probe=lambda pid: ["python", "/other/app.py"],
            )

    def test_registry_read_does_not_write_or_prune(self):
        path = Path(self.tmp.name, "active_sessions.json")
        original = json.dumps({"entries": [self.owner()]})
        path.write_text(original, encoding="utf-8")
        before = path.stat().st_mtime_ns
        self.assertEqual(cc.read_registry(path)[0]["lease_id"], "lease")
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(path.stat().st_mtime_ns, before)


class ProjectionTests(unittest.TestCase):
    def test_wrong_session_duplicate_gap_and_epoch_change(self):
        state = cc.ProjectionState("durable", "runtime")
        state.apply_replay(replay([event(1, "message.start")]))
        self.assertTrue(state.turn_active)
        state.apply_replay(replay([event(1, "message.start")], latest=1))
        self.assertEqual(state.last_seq, 1)
        self.assertTrue(state.apply_replay(replay([event(3, "tool.start", {"tool_id": "t", "name": "terminal"})], latest=3)))
        self.assertEqual(state.tools, {"t": "Terminal"})
        self.assertTrue(state.apply_replay(replay([], latest=0, epoch="epoch-2")))
        self.assertFalse(state.tools)
        with self.assertRaisesRegex(cc.ProtocolError, "cross-session"):
            state.apply_replay(replay([event(1, "message.start", session="other")], epoch="epoch-2"))

    def test_truncated_gap_clears_phase_and_tool(self):
        state = cc.ProjectionState("durable", "runtime")
        state.apply_replay(replay([
            event(1, "message.start"),
            event(2, "tool.start", {"tool_id": "one", "name": "terminal"}),
        ]))
        self.assertTrue(state.tools)
        state.apply_replay(replay([], latest=2, truncated=True))
        self.assertFalse(state.tools)
        self.assertFalse(state.turn_active)

    def test_concurrent_tools_complete_by_id_and_unknown_name_not_forwarded(self):
        state = cc.ProjectionState("durable", "runtime")
        state.apply_replay(replay([
            event(1, "message.start"),
            event(2, "tool.start", {"tool_id": "a", "name": "terminal", "args": {"secret": "x"}}),
            event(3, "tool.start", {"tool_id": "b", "name": "private_tool", "result": "secret"}),
            event(4, "tool.complete", {"tool_id": "a", "result": "secret"}),
        ]))
        self.assertEqual(state.tools, {"b": None})
        snap = state.snapshot(True)
        self.assertEqual(snap["activity"], "running")
        self.assertIsNone(snap["tool"])
        self.assertNotIn("secret", json.dumps(snap))

    def test_conservative_phase_and_idle_clears_tools(self):
        state = cc.ProjectionState("durable", "runtime")
        self.assertEqual(state.snapshot(True)["activity"], "running")
        state.apply_replay(replay([
            event(1, "message.start"), event(2, "thinking.delta", {"text": "private reasoning"}),
        ]))
        self.assertEqual(state.snapshot(True)["activity"], "thinking")
        self.assertIsNone(state.apply_status("fixture without canonical line"))
        self.assertEqual(state.snapshot(None)["activity"], "unknown")
        self.assertTrue(state.apply_status("Hermes TUI Status\nAgent Running: Yes"))
        state.apply_replay(replay([event(3, "tool.start", {"tool_id": "x", "name": "web_search"})]))
        self.assertFalse(state.apply_status("Hermes TUI Status\nAgent Running: No"))
        self.assertFalse(state.tools)
        self.assertEqual(state.snapshot(False)["activity"], "idle")

    def test_message_complete_only_records_durable_reconciliation_address(self):
        state = cc.ProjectionState("durable", "runtime")
        state.apply_replay(replay([
            event(1, "message.start"),
            event(2, "message.complete", {
                "text": "uncommitted fixture", "persisted_turn": {"final_assistant_row_id": 77},
            }),
        ]))
        self.assertEqual(state.wanted_row_id, 77)
        self.assertIsNone(state.reply)
        self.assertNotIn("uncommitted", json.dumps(state.snapshot(False)))

    def test_disconnect_unknown_retains_committed_reply_only(self):
        state = cc.ProjectionState("durable", "runtime", reply={"id": "9", "text": "committed"})
        state.apply_replay(replay([event(1, "message.start"), event(2, "message.delta", {"text": "stream"})]))
        state.disconnect()
        snap = state.snapshot(None)
        self.assertEqual(snap["activity"], "unknown")
        self.assertEqual(snap["reply"]["text"], "committed")
        self.assertNotIn("stream", json.dumps(snap))


class DurableAndOutputTests(unittest.TestCase):
    class FakeDB:
        rows = []
        calls = []

        def __init__(self, **kwargs): self.kwargs = kwargs
        def get_messages_as_conversation(self, session_id, **kwargs):
            self.calls.append((session_id, kwargs))
            return list(self.rows)
        def close(self): pass

    def store(self, redactor=lambda text, force: text):
        store = object.__new__(cc.HermesStore)
        store.profile_home = Path("/fixture/default")
        store._db_type = self.FakeDB
        store._redactor = redactor
        return store

    def test_sqlite_authoritative_final_reply_and_no_cross_session_read(self):
        self.FakeDB.calls.clear()
        self.FakeDB.rows = [
            {"role": "assistant", "content": "tool owner", "tool_calls": [{}], "_row_id": 1},
            {"role": "assistant", "content": "final", "_row_id": 2},
        ]
        reply = self.store().latest_reply("durable", wanted_row_id=2)
        self.assertEqual(reply, {"id": "2", "text": "final"})
        self.assertEqual(self.FakeDB.calls[0][0], "durable")
        self.assertEqual(self.FakeDB.calls[0][1], {
            "include_ancestors": True, "include_compacted": True, "include_row_ids": True,
        })
        self.assertIsNone(self.store().latest_reply("durable", wanted_row_id=999))

    def test_redaction_fail_closed_and_bounded_unicode(self):
        self.FakeDB.rows = [{"role": "assistant", "content": "secret", "_row_id": 3}]
        def broken(text, force): raise RuntimeError("fixture")
        self.assertIsNone(self.store(broken).latest_reply("durable"))
        text = "🙂" * 20_000
        bounded = cc.truncate_utf8(text)
        self.assertLessEqual(len(bounded.encode("utf-8")), cc.MAX_REPLY_BYTES)
        self.assertTrue(bounded)

    def test_atomic_output_has_exact_shape_and_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "staging", "companion.json")
            writer = cc.SnapshotWriter(path)
            snapshot = cc.unknown_snapshot()
            writer.write(snapshot)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), snapshot)
            self.assertEqual([item.name for item in path.parent.iterdir()], ["companion.json"])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_output_refuses_profile_source_and_named_profile_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "hermes_state.py").write_text("", encoding="utf-8")
            profile = root / ".hermes"
            profile.mkdir()
            outside = root / "stage" / "companion.json"
            got = cc.validate_locations(str(source), str(profile), str(outside))
            self.assertEqual(got[2], outside.resolve())
            for target in (profile / "companion.json", source / "companion.json"):
                with self.assertRaises(cc.CompanionError):
                    cc.validate_locations(str(source), str(profile), str(target))
            named = profile / "profiles" / "engineer"
            named.mkdir(parents=True)
            with self.assertRaisesRegex(cc.CompanionError, "named profiles"):
                cc.validate_locations(str(source), str(named), str(outside))

    def test_zero_canonical_snapshot_is_valid_unknown_sentinel(self):
        snapshot = cc.unknown_snapshot()
        self.assertEqual(snapshot, {
            "version": 1, "profile": "default", "session_id": "default:Bot Chat",
            "observed_at": snapshot["observed_at"], "activity": "unknown", "tool": None,
            "reply": None, "image": None, "cards": [],
        })


if __name__ == "__main__":
    unittest.main()
