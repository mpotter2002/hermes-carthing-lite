"""Persistent companion service: token persistence, device link and unit contract.

All HTTP and device responses here are labelled local fixtures, not live evidence.
"""
import configparser
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import companion_client as cc  # noqa: E402
import companion_service as service  # noqa: E402

ROTATED = {'access_token': 'new-a', 'refresh_token': 'new-r', 'token_type': 'Bearer',
           'expires_at': 4102444800, 'provider': 'basic', 'user_id': 'user'}


class TokenStoreTests(unittest.TestCase):
    def test_round_trip_keeps_only_refresh_identity_with_private_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = service.TokenStore(Path(tmp) / 'state')
            self.assertIsNone(store.load())
            store.save(cc.Tokens('access-secret', 'refresh-1', 99, 'basic', 'user'))
            saved = json.loads((Path(tmp) / 'state' / 'session.json').read_text())
            self.assertEqual(saved, {'provider': 'basic', 'refresh_token': 'refresh-1', 'user_id': 'user', 'version': 1})
            self.assertEqual(stat.S_IMODE(os.stat(Path(tmp) / 'state').st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(Path(tmp) / 'state' / 'session.json').st_mode), 0o600)
            loaded = store.load()
            self.assertEqual((loaded.access_token, loaded.refresh_token, loaded.expires_at, loaded.provider, loaded.user_id),
                             ('', 'refresh-1', 0, 'basic', 'user'))
            store.clear()
            self.assertIsNone(store.load())

    def test_refuses_shared_or_linked_session_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = service.TokenStore(Path(tmp) / 'state')
            store.save(cc.Tokens('', 'refresh-1', 0, 'basic', 'user'))
            target = Path(tmp) / 'state' / 'session.json'
            os.chmod(target, 0o640)
            with self.assertRaises(cc.AuthRequired):
                store.load()
            os.chmod(target, 0o600)
            other = Path(tmp) / 'elsewhere.json'
            target.rename(other)
            target.symlink_to(other)
            with self.assertRaises(cc.AuthRequired):
                store.load()

    def test_refuses_malformed_session_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = service.TokenStore(Path(tmp) / 'state')
            store.save(cc.Tokens('', 'refresh-1', 0, 'basic', 'user'))
            (Path(tmp) / 'state' / 'session.json').write_text('{"version": 1, "refresh_token": ""}')
            with self.assertRaises(cc.AuthRequired):
                store.load()


class PersistentAuthTests(unittest.TestCase):
    def test_loaded_refresh_only_session_rotates_and_persists_before_ticket(self):
        saved = []
        calls = []

        def fixture(url, **kwargs):
            calls.append(url)
            return ROTATED if url.endswith('/auth/native/refresh') else {'ticket': 'ticket-fixture'}

        tokens = cc.Tokens('', 'stored-r', 0, 'basic', 'user')
        auth = cc.AuthSession('https://gateway.example', tokens, on_rotate=lambda t: saved.append(t.refresh_token))
        with mock.patch.object(cc, '_http_json', side_effect=fixture):
            self.assertEqual(auth.ticket(), 'ticket-fixture')
        self.assertEqual(calls, ['https://gateway.example/auth/native/refresh', 'https://gateway.example/api/auth/ws-ticket'])
        self.assertEqual(saved, ['new-r'])

    def test_transient_refresh_failure_keeps_session_for_retry(self):
        for status in (None, 502, 503):
            with self.subTest(status=status):
                tokens = cc.Tokens('', 'stored-r', 0, 'basic', 'user')
                auth = cc.AuthSession('https://gateway.example', tokens)
                with mock.patch.object(cc, '_http_json', side_effect=cc.HttpFailure('native token refresh', status)):
                    with self.assertRaises(cc.HttpFailure):
                        auth.ticket()
                self.assertNotIsInstance(cc.HttpFailure('x', status), cc.AuthRequired)
                self.assertEqual(auth.tokens.refresh_token, 'stored-r')

    def test_rejected_refresh_clears_session(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                tokens = cc.Tokens('', 'stored-r', 0, 'basic', 'user')
                auth = cc.AuthSession('https://gateway.example', tokens)
                with mock.patch.object(cc, '_http_json', side_effect=cc.HttpFailure('native token refresh', status)):
                    with self.assertRaises(cc.AuthRequired):
                        auth.ticket()
                self.assertEqual(auth.tokens.refresh_token, '')


class FakeBridge:
    def __init__(self, fail_after=None):
        self.applied = []
        self.closed = False
        self.fail_after = fail_after

    def apply(self, snapshot):
        if self.fail_after is not None and len(self.applied) >= self.fail_after:
            raise RuntimeError('device command rejected')
        self.applied.append(snapshot)

    def close(self):
        self.closed = True


class DeviceLinkTests(unittest.TestCase):
    def make(self, states, bridges):
        clock = [0.0]
        commands = []

        def adb(*args):
            commands.append(args)
            if args[-1] == 'get-state':
                return states.pop(0) if states else 'device'
            return ''

        def factory():
            item = bridges.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        link = service.DeviceLink(adb=adb, bridge_factory=factory, clock=lambda: clock[0], log=lambda _m: None)
        return link, clock, commands

    def test_absent_device_backs_off_then_connects_without_navigation(self):
        bridge = FakeBridge()
        link, clock, commands = self.make(['', 'device'], [bridge])
        link.deliver({'n': 1})
        self.assertIsNone(link.bridge)
        link.deliver({'n': 2})
        self.assertEqual(sum(1 for c in commands if c[-1] == 'get-state'), 1, 'retry waits for backoff')
        clock[0] = 2.0
        link.deliver({'n': 3})
        self.assertIs(link.bridge, bridge)
        self.assertEqual(bridge.applied, [{'n': 3}])
        self.assertIn(('forward', 'tcp:19222', 'tcp:2222'), [c[-3:] for c in commands])
        self.assertEqual(link.status()['device_connects'], 1)

    def test_lost_delivery_drops_bridge_and_reconnects_after_backoff(self):
        first, second = FakeBridge(fail_after=1), FakeBridge()
        link, clock, _ = self.make([], [first, second])
        link.deliver({'n': 1})
        link.deliver({'n': 2})
        self.assertIsNone(link.bridge)
        self.assertTrue(first.closed)
        clock[0] = 2.0
        link.deliver({'n': 3})
        self.assertEqual(second.applied, [{'n': 3}])
        status = link.status()
        self.assertEqual((status['device_connects'], status['device_failures'], status['snapshots_applied']), (2, 1, 2))

    def test_wrong_page_or_missing_face_page_is_retried_with_capped_backoff(self):
        link, clock, _ = self.make([], [RuntimeError('expected one existing face preview page')] * 8 + [FakeBridge()])
        delays = []
        for _ in range(8):
            link.deliver({})
            delays.append(link.retry_at - clock[0])
            clock[0] = link.retry_at
        self.assertEqual(delays[:5], [2, 4, 8, 16, 30])
        self.assertEqual(max(delays), 30)
        link.deliver({'n': 'ok'})
        self.assertIsNotNone(link.bridge)


class ServiceRunTests(unittest.TestCase):
    def test_missing_login_exits_with_non_restarting_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = service.parse_args(['run', '--hermes-source', tmp, '--profile-home', tmp, '--origin', 'https://gateway.example',
                                       '--output', str(Path(tmp) / 'out' / 'companion.json'), '--state-dir', str(Path(tmp) / 'state')])
            with mock.patch.object(service.cc, 'validate_locations', return_value=(Path(tmp), Path(tmp), Path(tmp) / 'out' / 'companion.json')):
                self.assertEqual(service.run(args), service.EXIT_LOGIN_REQUIRED)
        self.assertEqual(service.EXIT_LOGIN_REQUIRED, 78)

    def test_persistent_preview_selector_accepts_only_final_face_page(self):
        self.assertEqual(service.FACE_URL, 'file:///var/hermes-face-v1-preview/device.html?companion=1&transport=push')


class UnitFileTests(unittest.TestCase):
    def test_user_unit_restarts_bounded_and_keeps_private_origin_out_of_repo(self):
        text = (ROOT / 'deploy' / 'hermes-carthing-companion.service').read_text()
        unit = configparser.ConfigParser(strict=False, interpolation=None)
        unit.read_string(text)
        section = unit['Service']
        self.assertEqual(section['Restart'], 'always')
        self.assertEqual(section['RestartPreventExitStatus'], '78')
        self.assertIn('MemoryMax', section)
        self.assertIn('CPUQuota', section)
        self.assertEqual(section['NoNewPrivileges'], 'yes')
        self.assertIn('EnvironmentFile', section)
        self.assertIn('companion_service.py run', section['ExecStart'])
        self.assertEqual(unit['Install']['WantedBy'], 'default.target')
        self.assertNotIn('ts.net', text)
        example = (ROOT / 'deploy' / 'hermes-carthing-companion.env.example').read_text()
        self.assertIn('HERMES_COMPANION_ORIGIN=https://gateway.example', example)


if __name__ == '__main__':
    unittest.main()
