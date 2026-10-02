import asyncio
import logging
import os
import threading
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_coding_cancellation as fixtures
from ev.config import DEFAULT_CONFIG
from ev.events import PhaxEventBus
from ev.tools import ToolContext, ToolRegistry, register_builtin_tools


class CodingToolLifecycleTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.CodingCancellationTests.setUp
    commit = fixtures.CodingCancellationTests.commit

    def registry(self):
        context = ToolContext(deepcopy(DEFAULT_CONFIG), PhaxEventBus(), logging.getLogger('fixture'),
                              coding_agent=self.gateway)
        registry = ToolRegistry(context)
        register_builtin_tools(registry)
        return registry

    def sleeping_cli(self):
        self.ready = self.base / 'codex.pid'
        self.fake.write_text('#!/usr/bin/python3\nimport sys,time,os\nfrom pathlib import Path\n'
            "if '--version' in sys.argv: print('fixture')\n"
            "elif sys.argv[1:3]==['login','status']: print('Logged in using test')\n"
            'else:\n Path(' + repr(str(self.ready)) + ').write_text(str(os.getpid()))\n time.sleep(60)\n')

    async def wait_ready(self):
        async with asyncio.timeout(3):
            while not self.ready.exists():
                await asyncio.sleep(.005)
        return int(self.ready.read_text())

    async def test_registry_cancellation_stops_and_joins_real_engineering_job(self):
        self.commit()
        self.sleeping_cli()
        registry = self.registry()
        with patch.object(self.gateway, '_codex_executable', return_value=str(self.fake)):
            proposal = self.gateway.propose('Fixture tool cancellation', str(self.root))
            spec = registry.get('development.coding_agent_execute')
            task = asyncio.create_task(registry.execute(spec, {'proposal_id': proposal['proposal_id']}))
            try:
                pid = await self.wait_ready()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            finally:
                self.gateway.cancel_all()
                await asyncio.gather(task, return_exceptions=True)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self.gateway.result(proposal['proposal_id'])['status'], 'CANCELLED')
        self.assertFalse(self.gateway._running)

    async def test_registry_deadline_stops_job_before_returning_timeout(self):
        self.commit()
        self.sleeping_cli()
        registry = self.registry()
        with patch.object(self.gateway, '_codex_executable', return_value=str(self.fake)):
            proposal = self.gateway.propose('Fixture tool deadline', str(self.root))
            spec = registry.get('development.coding_agent_execute')
            spec.timeout_seconds = 1
            task = asyncio.create_task(registry.execute(spec, {'proposal_id': proposal['proposal_id']}))
            try:
                pid = await self.wait_ready()
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(task, 5)
            finally:
                self.gateway.cancel_all()
                await asyncio.gather(task, return_exceptions=True)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(self.gateway._running)
        result = self.gateway.result(proposal['proposal_id'])
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertNotIn('commit_id', result)

    async def test_shared_cancel_before_thread_registration_never_runs_preflight_or_worktree(self):
        self.commit()
        with patch.object(self.gateway, '_codex_executable', return_value=str(self.fake)):
            proposal = self.gateway.propose('Fixture queued cancellation', str(self.root))
        cancel = threading.Event()
        cancel.set()
        with patch.object(self.gateway, 'status', side_effect=AssertionError('Cancelled work entered preflight')):
            result = await asyncio.to_thread(self.gateway.execute, proposal['proposal_id'], cancel_event=cancel)
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertFalse(result['executed'])
        self.assertNotIn('worktree', result)
        self.assertFalse(self.gateway._running)
