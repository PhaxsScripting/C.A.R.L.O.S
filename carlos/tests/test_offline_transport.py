import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from ev.ai.carlos_router import CarlosRouter
from ev.ai.offline import OfflineProvider


class RouterCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_stateless_local_provider_still_closes_cloud_transport(self):
        cloud = SimpleNamespace(close=AsyncMock())
        router = CarlosRouter(OfflineProvider(), cloud, lambda: 'NORMAL')
        await router.close()
        cloud.close.assert_awaited_once()

    async def test_local_cleanup_failure_does_not_leak_cloud_transport(self):
        local = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError('shutdown failed')))
        cloud = SimpleNamespace(close=AsyncMock())
        router = CarlosRouter(local, cloud, lambda: 'NORMAL')
        with self.assertRaisesRegex(RuntimeError, 'shutdown failed'):
            await router.close()
        cloud.close.assert_awaited_once()


class OfflineTransportTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux' and shutil.which('unshare'), 'Linux namespaces unavailable')
    def test_ipc_commands_and_cloud_failure_without_a_network_route(self):
        namespace = ['unshare', '--user', '--map-root-user', '--net']
        probe = subprocess.run(namespace + ['true'], capture_output=True, text=True, timeout=5)
        if probe.returncode:
            self.skipTest('This host does not permit unprivileged network namespaces')
        env = dict(os.environ, CARLOS_TEST_PARENT_NETNS=os.readlink('/proc/self/ns/net'))
        fixture = Path(__file__).with_name('fixtures') / 'offline_core_live.py'
        result = subprocess.run(namespace + [sys.executable, str(fixture)], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['network_namespace'], 'isolated; no route')
        operations = {check['operation']:check['status'] for check in report['checks']}
        self.assertEqual(len(operations), 9)
        self.assertEqual(operations['hardware metrics'], 'actual offline IPC; read-only sensors')
        self.assertEqual(operations['scoped dated history and natural request'], 'actual offline IPC; no mutations')
        self.assertEqual(operations['scoped task receipts and cross-project continuation refusal'], 'actual offline IPC; no task replay')
