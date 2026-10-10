"""Real loopback WebSocket transport with labelled fixture gateway payloads."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
import companion_client as cc

try:
    from websockets.asyncio.server import serve
except ImportError:
    serve = None


@unittest.skipIf(serve is None, 'Run with the existing managed interpreter for WebSocket tests')
class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_socket_reconnects_to_same_canonical_owner(self):
        for abrupt in (False, True):
            with self.subTest(abrupt=abrupt):
                connections = 0
                calls = []
                snapshots = []
                owner = cc.OwnerPin('durable', 'runtime', 'lease', 1, 1.0)
                reply = {'id': '7', 'text': 'Labelled fixture reply'}

                async def gateway(socket):
                    nonlocal connections
                    connections += 1
                    number = connections
                    await socket.send(json.dumps({'method': 'event', 'params': {
                        'type': 'gateway.ready', 'payload': {'replay_epoch': 'epoch-1'}}}))
                    async for raw in socket:
                        request = json.loads(raw)
                        calls.append(request)
                        if request['method'] == 'session.events.since':
                            result = {'events': [], 'latest_seq': 0, 'truncated': False,
                                      'count': 0, 'epoch': 'epoch-1', 'open_requests': []}
                        else:
                            result = {'output': 'Hermes TUI Status\nAgent Running: No'}
                        await socket.send(json.dumps({'id': request['id'], 'result': result}))
                        if number == 1 and request['method'] == 'session.status':
                            if abrupt:
                                socket.transport.abort()
                            else:
                                await socket.close(code=1001, reason='fixture disconnect')
                            return

                assert serve is not None
                async with serve(gateway, '127.0.0.1', 0, subprotocols=[cc.WS_PROTOCOL]) as server:
                    port = next(iter(server.sockets)).getsockname()[1]
                    client = object.__new__(cc.CompanionClient)
                    client.origin = f'http://127.0.0.1:{port}'
                    client.auth = mock.Mock()
                    client.auth.ticket.return_value = 'labelled-fixture-ticket'
                    client.store = mock.Mock()
                    client.store.canonical_id.return_value = owner.durable_id
                    client.store.latest_reply.return_value = reply
                    client.discover = lambda: (owner.durable_id, owner)
                    client._write_snapshot = lambda snapshot: snapshots.append(snapshot)
                    with mock.patch.object(cc, 'POLL_SECONDS', .01):
                        await client.run(1.1)
                self.assertGreaterEqual(connections, 2)
                activities = [snapshot['activity'] for snapshot in snapshots]
                self.assertIn('idle', activities)
                first_idle = activities.index('idle')
                self.assertIn('unknown', activities[first_idle + 1:])
                self.assertEqual(activities[-1], 'idle')
                self.assertEqual(snapshots[-1]['reply'], reply)
                self.assertTrue(all(snapshot['session_id'] == owner.durable_id
                                    for snapshot in snapshots[1:]))
                self.assertTrue(all(call['method'] in cc.RPC_METHODS for call in calls))
                self.assertTrue(all(call['params']['session_id'] == owner.runtime_id for call in calls))


if __name__ == '__main__':
    unittest.main()
