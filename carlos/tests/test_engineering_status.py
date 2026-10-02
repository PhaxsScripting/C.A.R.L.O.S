import asyncio
import json
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

import test_tool_cancellation as fixtures
from ev.commands import direct_action
from ev.engineering_queries import is_engineering_status_query, engineering_status_message
from ev.ipc.server import _is_responsive_request
from ev.permissions import Permission
from ev.state import CoreState
from ev.tools import ToolSpec


class EngineeringStatusTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ToolCancellationTests.setUp
    asyncTearDown = fixtures.ToolCancellationTests.asyncTearDown
    connection = fixtures.ToolCancellationTests.connection
    send = fixtures.ToolCancellationTests.send
    reply = fixtures.ToolCancellationTests.reply

    def seed(self):
        gateway = self.core.coding_agent
        ident = 'a' * 32
        gateway._proposals[ident] = {'proposal_id': ident, 'project': str(self.root / 'fixture'),
            'request': 'Fixture job', 'status': 'RUNNING', 'phase': 'VALIDATING', 'created_epoch': 1}
        gateway._running[ident] = threading.Event()
        gateway._running_started[ident] = time.monotonic() - 2
        gateway._status_cache = (time.monotonic(), {'available': True, 'installed': True})
        self.addCleanup(gateway._running.clear)
        return gateway, ident

    def test_exact_question_grammar_reuses_fast_command_and_ipc_routes(self):
        for text in ("What's Codex doing?", 'What is Carlos engineering working on?',
                     'check engineering status', 'is Codex still running'):
            self.assertTrue(is_engineering_status_query(text))
            self.assertEqual(direct_action(text).tool, 'development.coding_agent_status')
            self.assertTrue(_is_responsive_request({'type': 'command.submit', 'payload': {'text': text}}))
        for text in ('start Codex', 'show Codex how to delete files', 'what is codex doing and stop it',
                     'explain what is Codex doing', '"what is codex doing"'):
            self.assertFalse(is_engineering_status_query(text))

    def test_job_rows_are_fresh_even_while_cli_readiness_is_cached(self):
        gateway, ident = self.seed()
        first = gateway.status()
        first['tasks'][0]['status'] = 'FAKE_SUCCESS'
        first['active_tasks'][0]['phase'] = 'FAKE_PHASE'
        first['available'] = False
        gateway._proposals[ident]['phase'] = 'REVIEW_COMMIT'
        with patch.object(gateway, '_run', side_effect=AssertionError('Readiness reprobed')):
            latest = gateway.status()
        self.assertTrue(latest['available'])
        self.assertEqual(latest['tasks'][0]['status'], 'RUNNING')
        self.assertEqual(latest['active_tasks'][0]['phase'], 'REVIEW_COMMIT')
        self.assertGreaterEqual(latest['active_tasks'][0]['elapsed_ms'], 2000)
        gateway._running.clear()
        gateway._proposals[ident]['status'] = 'CANCELLED'
        stopped = gateway.status()
        self.assertEqual(stopped['active_job_count'], 0)
        self.assertEqual(stopped['tasks'][0]['status'], 'CANCELLED')
        self.assertIsNone(stopped['tasks'][0]['elapsed_ms'])

    def test_saved_running_receipt_cannot_be_reported_as_a_live_worker(self):
        gateway, ident = self.seed()
        gateway._proposals[ident]['status'] = 'CANCELLED'
        self.assertIn('finishing cleanup', engineering_status_message(gateway.status()))
        gateway._proposals[ident]['status'] = 'RUNNING'
        gateway._running.clear()
        message = engineering_status_message(gateway.status())
        self.assertIn('no live engineering worker', message)
        self.assertNotIn('is working', message)

    async def test_transcribed_question_finishes_its_state_without_starting_a_new_action(self):
        self.seed()
        self.core.state.transition(CoreState.LISTENING, 'fixture')
        self.core.state.transition(CoreState.TRANSCRIBING, 'fixture')
        result = await self.core._submit_action_clauses("What's Codex doing?", 'spoken-query')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.core.state.current, CoreState.DORMANT)
        self.assertEqual(self.core.task_journal.recent(), [])

    async def test_same_socket_question_returns_while_action_stays_owned_without_model_or_journal(self):
        gateway, ident = self.seed()
        entered = asyncio.Event()

        async def action(args, context):
            entered.set()
            await asyncio.sleep(60)

        spec = ToolSpec('fixture.action', 'FIXTURE', 'Blocked fixture', Permission.SAFE,
                        {'type': 'object'}, action)
        self.core.tools.register(spec)
        self.core.brain.submit = AsyncMock(side_effect=AssertionError('Status invoked model'))
        self.core._prepare_interactive_request = AsyncMock()
        await self.core.ipc.start()
        reader, writer = await self.connection()
        await self.send(writer, 'tool.call', 'blocked', {'name': spec.name, 'arguments': {}})
        await entered.wait()
        worker = next(iter(self.core._running_tool_tasks))
        self.core._interactive_task = worker
        self.core._prepare_interactive_request = AsyncMock(side_effect=AssertionError('Status interrupted action'))
        await self.send(writer, 'command.submit', 'status', {'text': "What's Codex doing?", 'speak': False})
        result = await self.reply(reader, 'status')
        self.assertEqual(result['response'], 'Codex is running validation for fixture.')
        self.assertFalse(result['execution']['changed_state'])
        self.assertFalse(worker.done())
        self.assertIs(self.core._interactive_task, worker)
        self.assertEqual(self.core.state.current, CoreState.USING_TOOL)
        self.core.brain.submit.assert_not_awaited()
        self.assertEqual(self.core.task_journal.recent(), [])
        self.core._interactive_task = None

    async def test_read_only_question_does_not_unlock_other_mutations_when_display_state_is_idle(self):
        self.seed()
        owner = asyncio.create_task(asyncio.sleep(60))
        self.core._interactive_task = owner
        mutation = AsyncMock(return_value={'verified': True})
        spec = ToolSpec('fixture.mutate', 'FIXTURE', 'Fixture mutation', Permission.SAFE,
                        {'type': 'object'}, mutation)
        self.core.tools.register(spec)
        try:
            result = await self.core._submit_action_clauses("What's Codex doing?", 'query')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(self.core.state.current, CoreState.DORMANT)
            blocked = await self.core.request_tool({'name': spec.name, 'arguments': {}}, 'other')
            self.assertEqual(blocked['status'], 'busy')
            mutation.assert_not_awaited()
        finally:
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            self.core._interactive_task = None

    async def test_status_questions_and_raw_apis_obey_local_private_and_guest_policy(self):
        self.seed()
        for mode in ('LOCAL ONLY', 'PRIVATE SESSION', 'GUEST'):
            with self.subTest(mode=mode):
                self.core.config['carlos']['privacy_mode'] = mode
                with patch.object(self.core.coding_agent, 'status', side_effect=AssertionError('Privacy leak')):
                    result = await self.core.handle_request({'type': 'command.submit', 'id': 'private',
                        'payload': {'text': "What's Codex doing?", 'speak': False}})
                    self.assertEqual(result['status'], 'denied')
                    raw = await self.core.handle_request({'type': 'coding.status', 'id': 'raw', 'payload': {}})
                    self.assertEqual(raw['status'], 'denied')

    async def test_slow_cli_status_does_not_block_health_and_discards_privacy_transition_result(self):
        entered, release = threading.Event(), threading.Event()

        def blocked():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test did not release probe')
            return {'tasks': [{'project': 'PRIVATE_CANARY'}]}

        self.core.coding_agent.status = blocked
        task = asyncio.create_task(self.core.handle_request({'type': 'coding.status', 'id': 'status', 'payload': {}}))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.005)
            self.assertTrue((await asyncio.wait_for(self.core.handle_request({'type': 'health', 'payload': {}}), .2))['ok'])
            self.core.config['carlos']['privacy_mode'] = 'GUEST'
            release.set()
            result = await task
            self.assertEqual(result['status'], 'denied')
            self.assertNotIn('PRIVATE_CANARY', json.dumps(result))
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
