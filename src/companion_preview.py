"""Reversible, bounded USB preview of the existing default Bot Chat."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

PREVIEW_URL = 'file:///var/hermes-face-v1-preview/device.html?companion=1&transport=push'
DEVICE_DIRECTORY = '/var/hermes-face-v1-preview/'


def device_snapshot(snapshot: dict, host_now: int, device_now: int) -> dict:
    age = host_now - snapshot['observed_at']
    if not 0 <= age <= 15000:
        raise ValueError('stale or future-dated snapshot')
    converted = dict(snapshot)
    converted['observed_at'] = device_now - age
    return converted


def validate_cdp_origin(origin: str) -> str:
    value = urllib.parse.urlsplit(origin)
    if value.scheme != 'http' or value.hostname not in ('localhost', '127.0.0.1') or value.username or value.password or value.path not in ('', '/') or value.query or value.fragment or not value.port:
        raise ValueError('CDP origin must be an existing loopback forward')
    return origin.rstrip('/')


class DeviceBridge:
    def __init__(self, origin: str, output: Path):
        from websockets.sync.client import connect
        self.connect = connect
        self.origin = validate_cdp_origin(origin)
        self.output = output
        self.applied = 0
        self.activities = set()
        self.copied = set()
        self.seq = 0
        self.socket = None
        page = self.page()
        self.previous = page['url']
        self.socket = connect(page['webSocketDebuggerUrl'], open_timeout=5, max_size=2**20, ping_interval=None)

    def page(self):
        pages = json.load(urllib.request.urlopen(self.origin + '/json/list', timeout=5))
        pages = [p for p in pages if p.get('type') == 'page' and p.get('url', '').startswith('file:///var/hermes-face-')]
        if len(pages) != 1:
            raise RuntimeError('expected one existing face preview page')
        address = urllib.parse.urlsplit(pages[0]['webSocketDebuggerUrl'])
        origin = urllib.parse.urlsplit(self.origin)
        if address.scheme != 'ws' or address.hostname not in ('localhost', '127.0.0.1') or address.port != origin.port or not address.path.startswith('/devtools/page/'):
            raise RuntimeError('unexpected device debugger endpoint')
        return pages[0]

    def call(self, method, params):
        if self.socket is None:
            raise RuntimeError('device debugger is disconnected')
        self.seq += 1
        wanted = self.seq
        self.socket.send(json.dumps({'id': wanted, 'method': method, 'params': params}))
        while True:
            response = json.loads(self.socket.recv(timeout=5))
            if response.get('id') == wanted:
                if 'error' in response:
                    raise RuntimeError('device command rejected')
                return response['result']

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
        if result.get('exceptionDetails'):
            raise RuntimeError('device expression failed')
        return result.get('result', {}).get('value')

    def prepare(self):
        self.call('Network.enable', {})
        self.call('Network.setCacheDisabled', {'cacheDisabled': True})
        self.call('Page.navigate', {'url': PREVIEW_URL})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.evaluate('!!(window.HermesCompanion && document.documentElement && document.querySelector("canvas[data-loaded=true]"))'):
                return
            time.sleep(.2)
        raise RuntimeError('device preview did not initialize')

    def apply(self, snapshot):
        image = snapshot.get('image')
        if image and image['src'] not in self.copied:
            name = image['src']
            if not re.fullmatch(r'[0-9a-f]{64}\.png', name):
                raise ValueError('invalid projected image name')
            source = self.output.parent / name
            from companion_content import _strict_png_dimensions
            if source.is_symlink() or source.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('invalid projected image file')
            _strict_png_dimensions(source.read_bytes())
            subprocess.run(['adb', 'push', str(source), DEVICE_DIRECTORY + name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            self.copied.add(name)
        converted = device_snapshot(snapshot, int(time.time() * 1000), int(self.evaluate('Date.now()')))
        encoded = json.dumps(converted, ensure_ascii=True, separators=(',', ':'))
        if self.evaluate('window.HermesCompanion.applySnapshot(' + encoded + ')') is not True:
            raise RuntimeError('device rejected snapshot')
        self.applied += 1
        self.activities.add(snapshot['activity'])
        if self.applied == 1:
            print('Device accepted first live snapshot.', flush=True)

    def restore(self):
        if self.socket:
            self.socket.close()
        page = self.page()
        self.socket = self.connect(page['webSocketDebuggerUrl'], open_timeout=5, max_size=2**20, ping_interval=None)
        try:
            self.call('Page.navigate', {'url': self.previous})
        finally:
            self.socket.close()


def managed_code(script: Path, args: list[str]) -> str:
    return f"import sys,runpy; sys._companion_preview_managed=True; sys.path.insert(0,{str(script.parent)!r}); sys.argv={([str(script)] + args)!r}; runpy.run_path({str(script)!r},run_name='__main__')"


def main(argv=None):
    extra = argparse.ArgumentParser(add_help=False)
    extra.add_argument('--cdp-origin', default='http://localhost:19222')
    options, remaining = extra.parse_known_args(argv)
    import companion_client as cc
    args = cc.parse_args(remaining)
    source, profile, output = cc.validate_locations(args.hermes_source, args.profile_home, args.output)
    cc.validate_origin(args.origin)
    validate_cdp_origin(options.cdp_origin)
    if not getattr(sys, '_companion_preview_managed', False):
        sys.path.insert(0, str(source))
        from hermes_cli._launchers import runtime_command
        code = managed_code(Path(__file__).resolve(), list(sys.argv[1:]))
        return subprocess.call(runtime_command(source, code=code))
    tokens = None
    bridge = None
    try:
        client_store = cc.HermesStore(source, profile)
        if client_store.canonical_id() is None:
            raise RuntimeError('default Bot Chat unavailable')
        cc.CALLBACK_TIMEOUT = 300
        tokens = cc.NativeLogin(args.origin, args.provider, timeout=300).login(lambda url: print('AUTHORIZE\n' + url, flush=True))
        client = cc.CompanionClient(source, profile, args.origin, output, cc.AuthSession(args.origin, tokens))
        durable, owner = client.discover()
        if not durable or not owner:
            raise RuntimeError('live canonical owner unavailable')
        bridge = DeviceBridge(options.cdp_origin, output)
        bridge.prepare()
        original_write = client.writer.write
        def deliver(snapshot):
            original_write(snapshot)
            bridge.apply(snapshot)
        client.writer.write = deliver
        asyncio.run(client.run(args.duration))
        print('Device delivery ' + json.dumps({'snapshots': bridge.applied, 'activities': sorted(bridge.activities)}), flush=True)
        return 0
    finally:
        if tokens:
            tokens.clear()
        if bridge:
            bridge.restore()
            print('Previous preview restored. Memory tokens cleared.', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
