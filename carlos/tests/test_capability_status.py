import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ev.paths import Paths
from ev.service import CarlosCore


class CapabilityStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.core = CarlosCore(paths=Paths(*(root / name for name in ('config', 'data', 'state', 'cache', 'run'))))
        self.core.coding_agent.status = lambda: {'available': False}
        self.command = AsyncMock()
        self.patches = [patch('ev.holosystem.command', self.command),
                        patch('ev.tools.holohand.status', AsyncMock(return_value={'state': 'UNVERIFIED'})),
                        patch('ev.remote_health.mobile_ready', AsyncMock(return_value=False))]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.core.daily.close()
        self.core.task_journal.close()
        self.core.memory.close()
        self.directory.cleanup()

    async def state(self, data, code=0):
        self.core.holosystem._checked = 0
        self.command.return_value = {'code': code, 'out': json.dumps(data), 'err': ''}
        result = await self.core.holosystem.capabilities()
        return result['capabilities']['Tailscale']

    async def test_failed_command_cannot_report_connected_from_stdout(self):
        data = {'BackendState': 'Running', 'Self': {'ID': 'fixture-node', 'TailscaleIPs': ['100.64.0.1']}}
        for code in (1, None):
            with self.subTest(code=code):
                self.assertEqual((await self.state(data, code))['state'], 'UNVERIFIED')

    async def test_running_without_valid_local_node_evidence_is_unverified(self):
        for node in (None, {}, {'ID': 'fixture-node'}, {'ID': '', 'TailscaleIPs': ['100.64.0.1']},
                     {'ID': 'fixture-node', 'TailscaleIPs': []},
                     {'ID': 'fixture-node', 'TailscaleIPs': ['not-an-address']},
                     {'ID': 'fixture-node', 'TailscaleIPs': [123]}):
            with self.subTest(node=node):
                self.assertEqual((await self.state({'BackendState': 'Running', 'Self': node}))['state'], 'UNVERIFIED')
        self.assertEqual((await self.state([]))['state'], 'UNVERIFIED')
        self.assertEqual((await self.state({'BackendState': True}))['state'], 'UNVERIFIED')

    async def test_connected_is_local_daemon_evidence_only_and_cache_is_isolated(self):
        data = {'BackendState': 'Running', 'Self': {'ID': 'fixture-node', 'TailscaleIPs': ['100.64.0.1', 'fd7a:115c:a1e0::1']}}
        row = await self.state(data)
        self.assertEqual(row['state'], 'CONNECTED')
        self.assertIn('remote reachability is unverified', row['evidence'])
        self.command.assert_awaited_once_with(['tailscale', 'status', '--json'], timeout=3, maximum=1048576)
        result = await self.core.holosystem.capabilities()
        result['capabilities']['Tailscale']['state'] = 'FAKE_STATE'
        result['capabilities'].clear()
        cached = await self.core.holosystem.capabilities()
        self.assertEqual(cached['capabilities']['Tailscale']['state'], 'CONNECTED')
        self.command.assert_awaited_once()
        self.assertNotIn('fixture-node', json.dumps(cached))
        status = self.core.holosystem.status()
        status['capabilities'].clear()
        self.assertEqual((await self.core.holosystem.capabilities())['capabilities']['Tailscale']['state'], 'CONNECTED')

    async def test_stopped_daemon_is_unavailable_and_invalid_json_is_unverified(self):
        self.assertEqual((await self.state({'BackendState': 'Stopped'}))['state'], 'UNAVAILABLE')
        self.core.holosystem._checked = 0
        self.command.return_value = {'code': 0, 'out': 'not json', 'err': ''}
        result = await self.core.holosystem.capabilities()
        self.assertEqual(result['capabilities']['Tailscale']['state'], 'UNVERIFIED')
