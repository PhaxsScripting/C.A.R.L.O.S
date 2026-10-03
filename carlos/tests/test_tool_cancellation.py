import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ev.paths import Paths
from ev.permissions import Permission
from ev.process_runner import command
from ev.service import CarlosCore
from ev.state import CoreState
from ev.tools import ToolSpec
from ev.voice.stt import Transcript
from ev.voice.normalization import is_conversation_stop
from ev.ipc.server import _is_responsive_request


class ToolCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.core = CarlosCore(paths=Paths(*(self.root / name for name in
                                           ('config', 'data', 'state', 'cache', 'run'))))
        self.core.power.cancel = AsyncMock(return_value={'cancelled': False})
        self.core.voice.end_conversation = AsyncMock()
        self.core.desktop.input.close = AsyncMock()
        self.writers = []

    async def asyncTearDown(self):
        for task in tuple(self.core._running_tool_tasks):
            task.cancel()
        if self.core._running_tool_tasks:
            await asyncio.gather(*self.core._running_tool_tasks, return_exceptions=True)
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        await self.core.ipc.stop()
        self.core.daily.close()
        self.core.memory.close()
        self.core.task_journal.close()
        self.directory.cleanup()

    def spec(self, execute, *, read_only=True, name='fixture.wait'):
        spec = ToolSpec(name, 'fixture', 'Owned test operation', Permission.SAFE,
                        {'type': 'object', 'properties': {}, 'additionalProperties': False},
                        execute, read_only=read_only, offline_available=True)
        self.core.tools.register(spec)
        return spec

    async def connection(self):
        reader, writer = await asyncio.open_unix_connection(self.core.paths.socket)
        self.writers.append(writer)
        await reader.readline()
        return reader, writer

    async def send(self, writer, kind, ident, payload):
        writer.write((json.dumps({'type': kind, 'id': ident, 'payload': payload}) + '\n').encode())
        await writer.drain()

    async def reply(self, reader, ident):
        async with asyncio.timeout(3):
            while True:
                line = await reader.readline()
                self.assertTrue(line, 'IPC closed before replying')
                result = json.loads(line)
                if result.get('id') == ident:
                    self.assertEqual(result['type'], 'response', result)
                    return result['payload']

    async def test_stop_all_reaps_owned_child_replies_and_keeps_same_client_alive(self):
        ready = self.root / 'child.pid'

        async def execute(args, context):
            return await command([sys.executable, '-c',
                'import os,time; from pathlib import Path; ready=Path(' + repr(str(ready))
                + '); staged=ready.with_suffix(".ready"); staged.write_text(str(os.getpid())); '
                + 'staged.replace(ready); time.sleep(60)'], timeout=60)

        spec = self.spec(execute)
        await self.core.ipc.start()
        reader, writer = await self.connection()
        stop_reader, stop_writer = await self.connection()
        await self.send(writer, 'tool.call', 'direct', {'name': spec.name, 'arguments': {}})
        async with asyncio.timeout(3):
            while not ready.exists():
                await asyncio.sleep(.005)
        pid = int(ready.read_text())
        await self.send(stop_writer, 'command.submit', 'stop', {'text': 'stop everything', 'speak': False})
        stopped = await self.reply(stop_reader, 'stop')
        result = await self.reply(reader, 'direct')
        self.assertEqual(stopped['tools'], {'cancel_requested': 1, 'cleanup_pending': 0})
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['execution']['changed_state'])
        self.assertFalse(result['in_flight_effects_may_complete'])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(self.core._running_tool_tasks)
        self.assertEqual(self.core.state.current, CoreState.DORMANT)
        await self.send(writer, 'health', 'after', {})
        self.assertTrue((await self.reply(reader, 'after'))['ok'])
        self.assertEqual(self.core.task_journal.recent(), [])

    async def test_cancelled_mutation_receipt_does_not_claim_rollback(self):
        entered = asyncio.Event()

        async def execute(args, context):
            entered.set()
            await asyncio.sleep(60)

        spec = self.spec(execute, read_only=False)
        self.core.task_journal.begin('action', 'Run test mutation')
        task = asyncio.create_task(self.core.execute_tool(spec, {}, 'action'))
        await entered.wait()
        await self.core._stop_all_actions('stop')
        result = await task
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['execution']['changed_state'])
        self.assertTrue(result['in_flight_effects_may_complete'])
        step = self.core.task_journal.get('action')['steps'][0]
        self.assertEqual(step['status'], 'CANCELLED')
        self.assertFalse(step['receipt']['execution']['verified'])
        self.assertIsNone(step['receipt']['execution']['changed_state'])

    async def test_cancel_during_journal_start_waits_for_id_and_closes_receipt(self):
        entered, release = threading.Event(), threading.Event()
        start = self.core.task_journal.start_step

        def delayed(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test failed to release journal')
            return start(*args)

        execute = AsyncMock(return_value={'ok': True})
        spec = self.spec(execute)
        self.core.task_journal.begin('journal', 'Run test observation')
        with patch.object(self.core.task_journal, 'start_step', side_effect=delayed):
            task = asyncio.create_task(self.core.execute_tool(spec, {}, 'journal'))
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.005)
            owned = next(iter(self.core._running_tool_tasks))
            owned.cancel()
            await asyncio.sleep(.01)
            owned.cancel()
            release.set()
            result = await asyncio.wait_for(task, 2)
        self.assertEqual(result['status'], 'cancelled')
        execute.assert_not_awaited()
        self.assertEqual(self.core.task_journal.get('journal')['steps'][0]['status'], 'CANCELLED')
        self.assertFalse(self.core._running_tool_tasks)

    async def test_cancel_after_execution_keeps_completed_receipt_and_evidence(self):
        entered, release = threading.Event(), threading.Event()
        finish = self.core.task_journal.finish_step

        def delayed(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test failed to release journal')
            return finish(*args)

        spec = self.spec(AsyncMock(return_value={'verified': True}), read_only=False)
        self.core.task_journal.begin('finished', 'Run test mutation')
        with patch.object(self.core.task_journal, 'finish_step', side_effect=delayed):
            task = asyncio.create_task(self.core.execute_tool(spec, {}, 'finished'))
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.005)
            next(iter(self.core._running_tool_tasks)).cancel()
            release.set()
            result = await asyncio.wait_for(task, 2)
        self.assertTrue(result['execution_finished'])
        self.assertTrue(result['execution']['verified'])
        self.assertEqual(self.core.task_journal.get('finished')['steps'][0]['status'], 'COMPLETED')

    async def test_external_cancellation_propagates_after_joined_cleanup(self):
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def execute(args, context):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(.01)
                cleaned.set()

        task = asyncio.create_task(self.core.execute_tool(self.spec(execute), {}, 'external'))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())
        self.assertFalse(self.core._running_tool_tasks)

    async def test_nested_tools_are_tracked_and_stopping_gate_blocks_new_dispatch(self):
        entered = asyncio.Event()

        async def inner(args, context):
            entered.set()
            await asyncio.sleep(60)

        child = self.spec(inner, name='fixture.child')

        async def outer(args, context):
            return await self.core.execute_tool(child, {}, 'nested', preserve_state=True)

        spec = self.spec(outer, name='fixture.parent')
        task = asyncio.create_task(self.core.execute_tool(spec, {}, 'nested'))
        await entered.wait()
        self.assertEqual(len(self.core._running_tool_tasks), 2)
        self.core._stopping_tools = True
        denied = await self.core.execute_tool(spec, {}, 'late')
        self.assertEqual(denied['status'], 'cancelled')
        stopped = await self.core._stop_all_actions('stop')
        self.assertEqual(stopped['tools']['cancel_requested'], 2)
        self.assertEqual((await task)['status'], 'cancelled')
        self.assertFalse(self.core._running_tool_tasks)
        self.assertFalse(self.core._stopping_tools)

    async def test_cancellation_does_not_overwrite_newer_state_owner(self):
        entered = asyncio.Event()

        async def execute(args, context):
            entered.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(self.core.execute_tool(self.spec(execute), {}, 'old'))
        await entered.wait()
        self.core._tool_state_owner = object()
        next(iter(self.core._running_tool_tasks)).cancel()
        self.assertEqual((await task)['status'], 'cancelled')
        self.assertEqual(self.core.state.current, CoreState.USING_TOOL)

    async def test_standalone_stop_phrases_cancel_direct_requests_without_model(self):
        entered = asyncio.Event()

        async def execute(args, context):
            entered.set()
            await asyncio.sleep(60)

        spec = self.spec(execute)
        self.core.brain.submit = AsyncMock(side_effect=AssertionError('Stop went to model'))
        for phrase in ('stop', 'wait', "actually don't", 'cancel that'):
            with self.subTest(phrase=phrase):
                entered.clear()
                task = asyncio.create_task(self.core.execute_tool(spec, {}, 'short-stop'))
                await entered.wait()
                stopped = await self.core.handle_request({'type': 'command.submit', 'id': 'stop',
                    'payload': {'text': phrase, 'speak': False}})
                self.assertEqual(stopped['tools']['cancel_requested'], 1)
                self.assertEqual((await task)['status'], 'cancelled')
        self.core.brain.submit.assert_not_awaited()
        for phrase in ("actually don't open Firefox", 'wait for Firefox to open',
                       'explain cancel that', "don't stop Spotify"):
            self.assertFalse(is_conversation_stop(phrase), phrase)

    async def test_spoken_stop_cancels_actions_inside_capture_lock_without_reentering_it(self):
        entered = asyncio.Event()

        async def execute(args, context):
            entered.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(self.core.execute_tool(self.spec(execute), {}, 'voice-stop', preserve_state=True))
        await entered.wait()
        manager = self.core.voice
        manager.stt.transcribe = AsyncMock(return_value=Transcript('Cancel that.', 'fixture', 'fixture.bin', 10))
        manager.capture_active = True
        manager.capture_origin = 'ambient'
        manager.capture_started = time.monotonic() - 1
        pcm = int(5000).to_bytes(2, 'little', signed=True) * 16000
        manager.capture_bytes = len(pcm)
        manager.capture_pcm.extend(pcm)
        manager.capture_speech_start_byte = 0
        manager.capture_speech_end_byte = len(pcm)
        manager.capture_mode = 'wake_command'
        manager.diagnostics.update({'max_rms': .1, 'max_peak': .2})
        self.core.state.transition(CoreState.LISTENING, 'fixture capture')
        manager.command_handler = AsyncMock(side_effect=AssertionError('Stop reached normal planner'))
        result = await asyncio.wait_for(manager.stop_capture('voice-stop'), 2)
        self.assertEqual(result['status'], 'conversation_ended')
        self.assertEqual((await task)['status'], 'cancelled')
        manager.command_handler.assert_not_awaited()
        manager.end_conversation.assert_not_awaited()
        self.assertFalse(self.core._running_tool_tasks)
        self.assertEqual(self.core.state.current, CoreState.DORMANT)

    async def test_stop_during_auto_approval_receipt_prevents_late_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        record = self.core.memory.record_permission

        def delayed(*args):
            if args[3].startswith('AUTO_APPROVED'):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('Test failed to release approval')
            return record(*args)

        execute = AsyncMock(return_value={'verified': True})
        spec = self.spec(execute, read_only=False)
        with patch.object(self.core.memory, 'record_permission', side_effect=delayed):
            task = asyncio.create_task(self.core.request_tool({'name': spec.name, 'arguments': {}}, 'late'))
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.005)
            stopped = await self.core._stop_all_actions('stop')
            self.assertEqual(stopped['tools']['cancel_requested'], 0)
            release.set()
            self.assertEqual((await task)['status'], 'cancelled')
        execute.assert_not_awaited()
        self.assertFalse(self.core._running_tool_tasks)

    async def test_stop_during_confirmation_resolution_prevents_approved_late_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        record = self.core.memory.record_permission

        def delayed(*args):
            if args[3] == 'APPROVED':
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('Test failed to release approval')
            return record(*args)

        execute = AsyncMock(return_value={'verified': True})
        spec = self.spec(execute, read_only=False)
        spec.requires_confirmation = True
        self.core.task_journal.begin('approve', 'Run test mutation')
        proposed = await self.core.request_tool({'name': spec.name, 'arguments': {}}, 'approve')
        confirmation = proposed['confirmation']
        with patch.object(self.core.memory, 'record_permission', side_effect=delayed):
            task = asyncio.create_task(self.core.resolve_confirmation({
                'id': confirmation['id'], 'approval_token': confirmation['approval_token'], 'approved': True}))
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(.005)
            await self.core._stop_all_actions('stop')
            release.set()
            self.assertEqual((await task)['status'], 'cancelled')
        execute.assert_not_awaited()
        self.assertEqual(self.core.task_journal.get('approve')['status'], 'CANCELLED')
        self.assertEqual(self.core.state.current, CoreState.DORMANT)

    async def test_dispatched_sync_action_may_finish_and_is_never_replayed(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        destination = self.root / 'fixture-effect'

        def execute(args, context):
            entered.set()
            if release.wait(3):
                destination.write_text('one write')
            finished.set()
            return {'verified': True}

        spec = self.spec(execute, read_only=False)
        task = asyncio.create_task(self.core.execute_tool(spec, {}, 'threaded'))
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(.005)
        try:
            await self.core._stop_all_actions('stop')
            result = await task
            self.assertTrue(result['in_flight_effects_may_complete'])
            self.assertIsNone(result['execution']['changed_state'])
            self.assertFalse(destination.exists())
        finally:
            release.set()
            async with asyncio.timeout(2):
                while not finished.is_set():
                    await asyncio.sleep(.005)
        self.assertEqual(destination.read_text(), 'one write')

    async def test_overlapping_stop_requests_keep_dispatch_gate_closed(self):
        first_release, second_release = asyncio.Event(), asyncio.Event()
        entered = asyncio.Queue()

        async def stop(correlation, **kwargs):
            entered.put_nowait(correlation)
            await (first_release if correlation == 'first' else second_release).wait()
            return {'status': 'conversation_ended'}

        self.core._finish_stop_all_actions = stop
        first = asyncio.create_task(self.core._stop_all_actions('first'))
        self.assertEqual(await entered.get(), 'first')
        second = asyncio.create_task(self.core._stop_all_actions('second'))
        self.assertEqual(await entered.get(), 'second')
        first_release.set()
        await first
        self.assertTrue(self.core._stopping_tools)
        execute = AsyncMock(return_value={'ok': True})
        self.assertEqual((await self.core.execute_tool(self.spec(execute), {}, 'late'))['status'], 'cancelled')
        execute.assert_not_awaited()
        second_release.set()
        await second
        self.assertFalse(self.core._stopping_tools)

    async def receive_ids(self, reader, ids):
        replies = {}
        async with asyncio.timeout(3):
            while set(replies) != set(ids):
                line = await reader.readline()
                self.assertTrue(line)
                reply = json.loads(line)
                if reply.get('id') in ids:
                    self.assertEqual(reply['type'], 'response', reply)
                    replies[reply['id']] = reply['payload']
        return replies

    async def test_same_connection_short_stop_discards_queued_actions_before_dispatch(self):
        entered = asyncio.Event()

        async def execute(args, context):
            entered.set()
            await asyncio.sleep(60)

        blocked = self.spec(execute)
        effect = AsyncMock(return_value={'verified': True})
        queued = self.spec(effect, read_only=False, name='fixture.effect')
        await self.core.ipc.start()
        reader, writer = await self.connection()
        for phrase in ('stop', 'wait', "actually don't", 'cancel that'):
            with self.subTest(phrase=phrase):
                entered.clear()
                await self.send(writer, 'tool.call', 'blocking', {'name': blocked.name, 'arguments': {}})
                await entered.wait()
                await self.send(writer, 'tool.call', 'queued-tool', {'name': queued.name, 'arguments': {}})
                await self.send(writer, 'command.submit', 'queued-command', {'text': 'open Firefox', 'speak': False})
                await self.send(writer, 'command.submit', 'stop', {'text': phrase, 'speak': False})
                replies = await self.receive_ids(reader, {'blocking', 'queued-tool', 'queued-command', 'stop'})
                self.assertEqual(replies['blocking']['status'], 'cancelled')
                for ident in ('queued-tool', 'queued-command'):
                    self.assertEqual(replies[ident], {'status': 'cancelled', 'queued': True,
                                                     'dispatched': False, 'reason': 'user_stop'})
                await self.send(writer, 'health', 'healthy', {})
                self.assertTrue((await self.reply(reader, 'healthy'))['ok'])
        effect.assert_not_awaited()

    async def test_stop_also_cancels_queued_actions_on_another_client(self):
        entered = asyncio.Event()

        async def execute(args, context):
            entered.set()
            await asyncio.sleep(60)

        blocked = self.spec(execute)
        effect = AsyncMock(return_value={'verified': True})
        queued = self.spec(effect, read_only=False, name='fixture.effect')
        await self.core.ipc.start()
        reader, writer = await self.connection()
        stop_reader, stop_writer = await self.connection()
        await self.send(writer, 'tool.call', 'blocking', {'name': blocked.name, 'arguments': {}})
        await entered.wait()
        await self.send(writer, 'tool.call', 'queued', {'name': queued.name, 'arguments': {}})
        async with asyncio.timeout(2):
            while not any(queue.qsize() for queue in self.core.ipc._request_queues.values()):
                await asyncio.sleep(.005)
        await self.send(stop_writer, 'command.submit', 'stop', {'text': 'stop everything', 'speak': False})
        self.assertEqual((await self.reply(stop_reader, 'stop'))['tools']['cancel_requested'], 1)
        replies = await self.receive_ids(reader, {'blocking', 'queued'})
        self.assertEqual(replies['queued']['status'], 'cancelled')
        self.assertFalse(replies['queued']['dispatched'])
        effect.assert_not_awaited()

    async def test_stop_has_reserved_capacity_when_status_slots_are_busy(self):
        entered, release = asyncio.Event(), asyncio.Event()
        status_count = 0
        handle = self.core.handle_request

        async def handler(request):
            nonlocal status_count
            if request['type'] == 'health' and request['id'].startswith('busy'):
                status_count += 1
                await release.wait()
            return await handle(request)

        async def execute(args, context):
            entered.set()
            await asyncio.sleep(60)

        blocked = self.spec(execute)
        self.core.ipc.handler = handler
        await self.core.ipc.start()
        reader, writer = await self.connection()
        await self.send(writer, 'tool.call', 'blocking', {'name': blocked.name, 'arguments': {}})
        await entered.wait()
        try:
            for index in range(8):
                await self.send(writer, 'health', 'busy' + str(index), {})
            async with asyncio.timeout(2):
                while status_count != 8:
                    await asyncio.sleep(.005)
            await self.send(writer, 'command.submit', 'stop', {'text': 'cancel that', 'speak': False})
            replies = await self.receive_ids(reader, {'blocking', 'stop'})
            self.assertEqual(replies['blocking']['status'], 'cancelled')
            self.assertFalse(release.is_set())
        finally:
            release.set()

    def test_only_exact_action_stop_grammar_preempts_the_queue(self):
        for text in ('stop', 'Wait.', "Actually don't!", 'cancel that', 'stop everything'):
            self.assertTrue(_is_responsive_request({'type': 'command.submit', 'payload': {'text': text}}))
        for text in ('stop Spotify', 'wait for Firefox', "actually don't open Firefox", 'tell me about stop'):
            self.assertFalse(_is_responsive_request({'type': 'command.submit', 'payload': {'text': text}}))
