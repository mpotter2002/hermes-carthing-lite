"""Persistent read-only Car Thing companion for the default Bot Chat.

`login` performs the existing native sign-in once and saves only the rotating
refresh token in a private state directory. `run` restores that session, mirrors
the canonical default Bot Chat with the existing client, and pushes snapshots to
the Car Thing's boot face page whenever the device is attached.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion_client as cc  # noqa: E402
from companion_preview import DeviceBridge, PREVIEW_URL, managed_code  # noqa: E402

CDP_ORIGIN = 'http://localhost:19222'
EXIT_LOGIN_REQUIRED = 78
RUN_FOREVER = 10 * 365 * 24 * 3600.0
STATUS_INTERVAL = 60.0
MAX_BACKOFF = 30.0


def log(message: str) -> None:
    print(time.strftime('%Y-%m-%dT%H:%M:%S%z ') + message, flush=True)


class TokenStore:
    """Private on-disk refresh session: one JSON file, mode 0600, in a 0700 directory."""

    NAME = 'session.json'

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _open_directory(self) -> int:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            os.close(fd)
            raise cc.AuthRequired('companion state directory is not owned by this user')
        if info.st_mode & 0o077:
            os.fchmod(fd, 0o700)
        return fd

    def load(self) -> cc.Tokens | None:
        fd = self._open_directory()
        try:
            try:
                handle = os.open(self.NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                return None
            except OSError:
                raise cc.AuthRequired('companion session file is not a regular private file') from None
            with os.fdopen(handle, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise cc.AuthRequired('companion session file is not a regular private file')
                raw = stream.read(8193)
        finally:
            os.close(fd)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise cc.AuthRequired('companion session file is invalid') from None
        fields = ('provider', 'refresh_token', 'user_id', 'version')
        if len(raw) > 8192 or not isinstance(value, dict) or sorted(value) != list(fields) or value['version'] != 1 \
                or not all(isinstance(value[key], str) for key in fields[:3]) or not value['refresh_token']:
            raise cc.AuthRequired('companion session file is invalid')
        return cc.Tokens('', value['refresh_token'], 0, value['provider'], value['user_id'])

    def save(self, tokens: cc.Tokens) -> None:
        data = json.dumps({'provider': tokens.provider, 'refresh_token': tokens.refresh_token,
                           'user_id': tokens.user_id, 'version': 1}, sort_keys=True).encode()
        fd = self._open_directory()
        temporary = '.' + self.NAME + '.' + os.urandom(6).hex() + '.tmp'
        try:
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(handle, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.NAME, src_dir_fd=fd, dst_dir_fd=fd)
                temporary = None
                os.fsync(fd)
            finally:
                if temporary:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(fd)

    def clear(self) -> None:
        fd = self._open_directory()
        try:
            os.unlink(self.NAME, dir_fd=fd)
            os.fsync(fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(fd)


def run_adb(*args: str) -> str:
    result = subprocess.run(['adb', *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15)
    return result.stdout.strip() if result.returncode == 0 else ''


class DeviceLink:
    """Delivers snapshots to the boot face page; tolerates USB absence and device reboots."""

    def __init__(self, output: Path | None = None, serial: str = '', adb=run_adb, bridge_factory=None,
                 clock=time.monotonic, log=log):
        self.serial = serial
        self.adb = adb
        self.bridge_factory = bridge_factory or (lambda: DeviceBridge(CDP_ORIGIN, output, allowed_urls={PREVIEW_URL}))
        self.clock = clock
        self.log = log
        self.bridge = None
        self.retry_at = 0.0
        self.backoff = 2.0
        self.last_reason = None
        self.counts = {'device_connects': 0, 'device_failures': 0, 'snapshots_applied': 0}
        self.last_applied = None

    def _adb(self, *args: str) -> str:
        return self.adb(*(('-s', self.serial) if self.serial else ()), *args)

    def _connect(self) -> None:
        if self._adb('get-state') != 'device':
            raise RuntimeError('Car Thing not attached')
        self._adb('forward', 'tcp:19222', 'tcp:2222')
        bridge = self.bridge_factory()
        wait = getattr(bridge, 'wait_ready', None)
        if wait:
            try:
                wait()
            except Exception:
                bridge.close()
                raise
        self.bridge = bridge
        self.backoff = 2.0
        self.last_reason = None
        self.counts['device_connects'] += 1
        self.log('Car Thing face connected.')

    def _wait(self, reason: str) -> None:
        if reason != self.last_reason:
            self.log('Car Thing face unavailable: ' + reason + '; retrying.')
            self.last_reason = reason
        self.retry_at = self.clock() + self.backoff
        self.backoff = min(self.backoff * 2, MAX_BACKOFF)

    def deliver(self, snapshot: dict) -> None:
        if self.bridge is None:
            if self.clock() < self.retry_at:
                return
            try:
                self._connect()
            except Exception as exc:
                self._wait(str(exc) or exc.__class__.__name__)
                return
        bridge = self.bridge
        try:
            bridge.apply(snapshot)
        except Exception:
            bridge.close()
            self.bridge = None
            self.counts['device_failures'] += 1
            self._wait('device delivery lost')
            return
        self.counts['snapshots_applied'] += 1
        self.last_applied = time.time()

    def status(self) -> dict:
        return dict(self.counts, device_connected=self.bridge is not None, last_applied_at=self.last_applied)


class StatusFile:
    def __init__(self, directory: Path, link: DeviceLink):
        self.path = Path(directory) / 'status.json'
        self.link = link
        self.started = time.time()
        self.next_write = 0.0
        self.activity = None

    def update(self, snapshot: dict) -> None:
        self.activity = snapshot.get('activity')
        now = time.monotonic()
        if now < self.next_write:
            return
        self.next_write = now + STATUS_INTERVAL
        value = dict(self.link.status(), started_at=self.started, updated_at=time.time(), activity=self.activity,
                     max_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        temporary = self.path.with_name('.status.json.tmp')
        temporary.write_text(json.dumps(value, sort_keys=True))
        os.replace(temporary, self.path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Persistent read-only Car Thing companion')
    parser.add_argument('command', choices=('login', 'run'))
    parser.add_argument('--hermes-source', required=True)
    parser.add_argument('--profile-home', required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--provider', default='')
    parser.add_argument('--serial', default='')
    parser.add_argument('--state-dir', default=str(Path.home() / '.local' / 'state' / 'hermes-carthing-companion'))
    return parser.parse_args(argv)


def login(args) -> int:
    origin = cc.validate_origin(args.origin)
    tokens = cc.NativeLogin(origin, args.provider, timeout=300).login(lambda url: print('AUTHORIZE\n' + url, flush=True))
    try:
        TokenStore(Path(args.state_dir)).save(tokens)
    finally:
        tokens.clear()
    log('Companion login saved.')
    return 0


def run(args) -> int:
    source, profile, output = cc.validate_locations(args.hermes_source, args.profile_home, args.output)
    origin = cc.validate_origin(args.origin)
    store = TokenStore(Path(args.state_dir))
    try:
        tokens = store.load()
    except cc.AuthRequired as exc:
        log(str(exc) + '; run companion_service.py login.')
        return EXIT_LOGIN_REQUIRED
    if tokens is None:
        log('Companion login required; run companion_service.py login.')
        return EXIT_LOGIN_REQUIRED
    auth = cc.AuthSession(origin, tokens, on_rotate=store.save)
    client = cc.CompanionClient(source, profile, origin, output, auth)
    link = DeviceLink(output, args.serial)
    status = StatusFile(Path(args.state_dir), link)
    write = client.writer.write

    def deliver(snapshot):
        write(snapshot)
        link.deliver(snapshot)
        try:
            status.update(snapshot)
        except OSError:
            pass

    client.writer.write = deliver
    log('Companion feed started.')
    try:
        asyncio.run(client.run(RUN_FOREVER))
    except cc.AuthRequired as exc:
        store.clear()
        log(str(exc) + '; saved session cleared.')
        return EXIT_LOGIN_REQUIRED
    finally:
        auth.tokens.clear()
        if link.bridge:
            link.bridge.close()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if not getattr(sys, '_companion_preview_managed', False):
        source, _profile, _output = cc.validate_locations(args.hermes_source, args.profile_home, args.output)
        sys.path.insert(0, str(source))
        from hermes_cli._launchers import runtime_command
        code = managed_code(Path(__file__).resolve(), list(sys.argv[1:] if argv is None else argv))
        return subprocess.call(runtime_command(source, code=code))
    return login(args) if args.command == 'login' else run(args)


if __name__ == '__main__':
    raise SystemExit(main())
