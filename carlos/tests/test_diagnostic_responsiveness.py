import asyncio
import threading
import unittest
from unittest.mock import patch

import test_tool_cancellation as fixtures
from ev.ipc.server import _is_responsive_request


class DiagnosticResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ToolCancellationTests.setUp
    asyncTearDown = fixtures.ToolCancellationTests.asyncTearDown
    connection = fixtures.ToolCancellationTests.connection
    send = fixtures.ToolCancellationTests.send
    reply = fixtures.ToolCancellationTests.reply

    async def exercise(self, kind, backend):
        entered, release = threading.Event(), threading.Event()
        action_entered = asyncio.Event()
        owner = threading.get_ident()
        observed_threads = []

        def probe():
            observed_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test did not release diagnostic probe')
            return {'available': False, 'status': 'UNAVAILABLE', 'reason': 'Fixture backend'}

        async def action(arguments, context):
            action_entered.set()
            await asyncio.Event().wait()

        spec = fixtures.ToolCancellationTests.spec(self, action)
        await self.core.ipc.start()
        reader, writer = await self.connection()
        await self.send(writer, 'tool.call', 'action', {'name': spec.name, 'arguments': {}})
        await action_entered.wait()
        worker = next(iter(self.core._running_tool_tasks))
        builder_name = 'capability_query' if kind == 'capability.query' else 'self_diagnostics'
        builder = getattr(self.core, builder_name)

        def build(*args, **kwargs):
            self.assertEqual(threading.get_ident(), owner)
            self.assertEqual(kwargs[backend + '_status']['reason'], 'Fixture backend')
            return builder(*args, **kwargs)

        target = self.core.coding_agent if backend == 'coding' else self.core.accessibility
        with patch.object(target, 'status', side_effect=probe), patch.object(self.core, builder_name, side_effect=build):
            try:
                await self.send(writer, kind, 'diagnostic', {'query': 'screen'})
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(.005)
                await self.send(writer, 'health', 'health', {})
                self.assertTrue((await self.reply(reader, 'health'))['ok'])
                self.assertFalse(worker.done())
                release.set()
                result = await self.reply(reader, 'diagnostic')
                self.assertIn('matches' if kind == 'capability.query' else 'checks', result)
                self.assertFalse(worker.done())
                self.assertTrue(_is_responsive_request({'type': kind}))
                self.assertTrue(all(ident != owner for ident in observed_threads))
            finally:
                release.set()

    async def test_capability_query_keeps_health_responsive_during_cli_probe(self):
        await self.exercise('capability.query', 'coding')

    async def test_diagnostics_keep_health_responsive_during_cli_probe(self):
        await self.exercise('self.diagnostics', 'coding')

    async def test_capability_query_keeps_health_responsive_during_accessibility_probe(self):
        await self.exercise('capability.query', 'accessibility')

    async def test_diagnostics_keep_health_responsive_during_accessibility_probe(self):
        await self.exercise('self.diagnostics', 'accessibility')
