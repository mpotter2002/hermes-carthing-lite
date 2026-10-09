import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import companion_preview as preview

class PreviewTests(unittest.TestCase):
    def test_device_selector_rejects_other_face_pages_and_replaced_targets(self):
        from unittest.mock import patch, Mock
        bridge = preview.DeviceBridge.__new__(preview.DeviceBridge)
        bridge.origin = 'http://localhost:19222'
        bridge.target_id = None
        expected = {'id':'chosen', 'type':'page', 'url':'file:///var/hermes-face-preview/device.html?demo=idle', 'webSocketDebuggerUrl':'ws://localhost:19222/devtools/page/chosen'}
        with patch.object(preview.urllib.request, 'urlopen', return_value=Mock()), patch.object(preview.json, 'load', return_value=[dict(expected, url='file:///var/hermes-face-unrelated/private.html')]):
            with self.assertRaises(RuntimeError):
                bridge.page()
        bridge.target_id = 'chosen'
        with patch.object(preview.urllib.request, 'urlopen', return_value=Mock()), patch.object(preview.json, 'load', return_value=[dict(expected, id='replacement', webSocketDebuggerUrl='ws://localhost:19222/devtools/page/replacement')]):
            with self.assertRaises(RuntimeError):
                bridge.page()

    def test_device_prepare_requires_exact_final_url(self):
        bridge = preview.DeviceBridge.__new__(preview.DeviceBridge)
        bridge.call = lambda *args: None
        def evaluate(expression):
            if expression == 'location.href':
                return 'file:///var/hermes-face-unrelated/private.html'
            return True
        bridge.evaluate = evaluate
        with self.assertRaises(RuntimeError):
            bridge.prepare()

    def test_exit_clears_current_credentials_after_rotation(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch, Mock
        import companion_client as cc
        for fails in (False, True):
            with self.subTest(fails=fails):
                initial = cc.Tokens('initial-access', 'initial-refresh', 0, 'basic', 'user')
                rotated = cc.Tokens('rotated-access', 'rotated-refresh', 0, 'basic', 'user')
                args = SimpleNamespace(hermes_source='source', profile_home='profile', output='output', origin='https://example.test', provider=None, duration=1)
                client = Mock()
                client.discover.return_value = ('canonical', 'owner')
                async def run(duration):
                    client.auth.tokens = rotated
                    if fails:
                        raise RuntimeError('test exit')
                client.run = run
                def make_client(source, profile, origin, output, auth):
                    client.auth = auth
                    return client
                with patch.object(sys, '_companion_preview_managed', True, create=True), patch.object(cc, 'parse_args', return_value=args), patch.object(cc, 'validate_locations', return_value=(Path('/source'), Path('/profile'), Path('/output/companion.json'))), patch.object(cc, 'HermesStore') as store, patch.object(cc, 'NativeLogin') as login, patch.object(cc, 'CompanionClient', side_effect=make_client), patch.object(preview, 'DeviceBridge') as device:
                    device.return_value.applied = 0
                    device.return_value.activities = set()
                    store.return_value.canonical_id.return_value = 'canonical'
                    login.return_value.login.return_value = initial
                    if fails:
                        with self.assertRaises(RuntimeError):
                            preview.main([])
                    else:
                        self.assertEqual(preview.main([]), 0)
                self.assertEqual((rotated.access_token, rotated.refresh_token), ('', ''))
                self.assertEqual((initial.access_token, initial.refresh_token), ('', ''))

    def test_image_transfer_uses_validated_bytes_despite_path_mutation(self):
        import hashlib
        import tempfile
        from unittest.mock import patch
        from test_companion_content import png_bytes
        for mode in ('replace', 'modify'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                data = png_bytes()
                name = hashlib.sha256(data).hexdigest() + '.png'
                source = Path(directory) / name
                source.write_bytes(data)
                bridge = preview.DeviceBridge.__new__(preview.DeviceBridge)
                bridge.output = Path(directory) / 'companion.json'
                bridge.copied = set()
                bridge.applied = 0
                bridge.activities = set()
                bridge.evaluate = lambda expression: int(__import__('time').time() * 1000) if expression == 'Date.now()' else True
                def transfer(command, **kwargs):
                    if mode == 'replace':
                        source.unlink()
                        source.symlink_to(Path(directory) / 'private-file')
                    else:
                        source.write_bytes(b'not the validated image')
                    self.assertEqual(command[:2], ['adb', 'push'])
                    descriptors = kwargs.get('pass_fds', ())
                    self.assertEqual(len(descriptors), 1, 'ADB must receive a stable sealed descriptor')
                    descriptor = descriptors[0]
                    import os
                    self.assertEqual(command[2], '/proc/self/fd/' + str(descriptor))
                    self.assertEqual(os.pread(descriptor, len(data) + 1, 0), data)
                    with self.assertRaises(PermissionError):
                        os.write(descriptor, b'changed')
                import fcntl
                with patch.object(preview.subprocess, 'run', side_effect=transfer), patch.dict(fcntl.__dict__, {'fcntl': fcntl.fcntl}, clear=True):
                    bridge.apply({'image': {'src': name, 'alt': 'test'}, 'activity': 'idle', 'observed_at': int(__import__('time').time() * 1000)})
                self.assertEqual(bridge.copied, {name})

    def test_projected_image_digest_mismatch_is_rejected(self):
        import tempfile
        from unittest.mock import patch
        from test_companion_content import png_bytes
        with tempfile.TemporaryDirectory() as directory:
            name = '0' * 64 + '.png'
            (Path(directory) / name).write_bytes(png_bytes())
            bridge = preview.DeviceBridge.__new__(preview.DeviceBridge)
            bridge.output = Path(directory) / 'companion.json'
            bridge.copied = set()
            bridge.applied = 0
            bridge.activities = set()
            bridge.evaluate = lambda expression: int(__import__('time').time() * 1000) if expression == 'Date.now()' else True
            with patch.object(preview.subprocess, 'run') as transfer:
                with self.assertRaises(ValueError):
                    bridge.apply({'image': {'src': name}, 'activity': 'idle', 'observed_at': int(__import__('time').time() * 1000)})
                transfer.assert_not_called()

    def test_isolated_launcher_imports_client_from_script_directory(self):
        import subprocess
        script = Path(preview.__file__).resolve()
        result = subprocess.run([sys.executable, '-I', '-c', preview.managed_code(script, ['--help'])], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Read-only default Bot Chat companion client', result.stdout)

    def test_clock_translation_preserves_age(self):
        source = {'observed_at': 990, 'activity': 'idle'}
        converted = preview.device_snapshot(source, 1000, 500)
        self.assertEqual(converted['observed_at'], 490)
        self.assertEqual(source['observed_at'], 990)
        self.assertEqual(converted['activity'], 'idle')

    def test_stale_and_future_snapshots_rejected(self):
        for timestamp in (1001, -14001):
            with self.assertRaises(ValueError):
                preview.device_snapshot({'observed_at': timestamp}, 1000, 500)

    def test_cdp_origin_is_loopback_only(self):
        self.assertEqual(preview.validate_cdp_origin('http://localhost:19222'), 'http://localhost:19222')
        for origin in ('https://gateway.example', 'http://localhost:19222/path', 'http://localhost:19222?query=1'):
            with self.assertRaises(ValueError):
                preview.validate_cdp_origin(origin)
