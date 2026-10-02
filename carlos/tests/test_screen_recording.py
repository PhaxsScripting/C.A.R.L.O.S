import asyncio
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import test_tool_cancellation as fixtures
from ev.commands import direct_action
from ev.ipc.server import _is_responsive_request
from ev.recording import ScreenRecorder
from ev.state import CoreState


REPORT = {'frames': 15, 'width': 1280, 'height': 720, 'duration_seconds': 1.0, 'encoder': 'x264enc'}


def worker(root, *, blocked=False):
    path = root / 'fixture-worker.py'
    path.write_text('import os,sys,json,time\nfrom pathlib import Path\n'
        'if sys.argv[1] == "--probe":\n print(json.dumps({"available":True,"reason":"Fixture probe"})); sys.exit(0)\n'
        'out=Path(sys.argv[1]); out.write_bytes(b"\\x1aE\\xdf\\xa3fixture")\n'
        f'Path({str(root / "worker.pid")!r}).write_text(str(os.getpid()))\n'
        + ('time.sleep(30)\n' if blocked else '')
        + 'print(json.dumps(' + repr(REPORT) + '))\n')
    return path


class ScreenRecordingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.recorder = ScreenRecorder(self.root / 'clips', python=sys.executable)
        self.recorder.worker = worker(self.root)

    def tearDown(self):
        self.directory.cleanup()

    async def test_clip_is_published_after_checks_with_private_modes_and_hash(self):
        result = await self.recorder.capture(1)
        self.assertTrue(result['verified'])
        self.assertFalse(result['audio_recorded'])
        self.assertFalse(result['network_exposed'])
        self.assertEqual(len(result['sha256']), 64)
        path = Path(result['path'])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(self.recorder.list()['recordings'][0]['recording_id'], result['recording_id'])
        self.assertTrue(self.recorder.delete(result['recording_id'])['removed'])
        self.assertFalse(self.recorder.delete(result['recording_id'])['removed'])
        self.assertFalse(self.recorder.active)

    async def test_invalid_metadata_and_failed_worker_discard_only_pending_clip(self):
        original = await self.recorder.capture(1)
        source = self.recorder.worker.read_text()
        for replacement in ("{'frames':0}", "{'frames':15,'width':1280,'height':720,'duration_seconds':float('nan'),'encoder':'x264enc'}"):
            self.recorder.worker.write_text(source[:source.index('print(json.dumps(' + repr(REPORT))]
                + 'print(json.dumps(' + replacement + '))\n')
            with self.assertRaises(ValueError):
                await self.recorder.capture(1)
            self.assertEqual(len(self.recorder.list()['recordings']), 1)
            self.assertTrue(Path(original['path']).exists())
            self.assertFalse(list(self.recorder.root.glob('*.pending.mkv')))
            self.assertFalse(self.recorder.active)

    async def test_duration_and_missing_dependency_do_not_start_capture(self):
        for value in (True, 0, 121, 1.0, '10'):
            with self.assertRaises(ValueError):
                await self.recorder.capture(value)
        self.recorder.status = AsyncMock(return_value={'available': False, 'reason': 'No portal'})
        with self.assertRaisesRegex(RuntimeError, 'No portal'):
            await self.recorder.capture(1)
        self.assertFalse((self.root / 'worker.pid').exists())
        self.assertFalse(self.recorder.active)

    async def test_cancel_reaps_worker_ignores_unfinished_clip_and_unlocks_recorder(self):
        self.recorder.worker = worker(self.root, blocked=True)
        task = asyncio.create_task(self.recorder.capture(1))
        try:
            async with asyncio.timeout(3):
                while not (self.root / 'worker.pid').exists():
                    await asyncio.sleep(.005)
            pid = int((self.root / 'worker.pid').read_text())
            self.assertTrue(self.recorder.active)
            self.assertEqual(self.recorder.list()['recordings'], [])
            with self.assertRaisesRegex(RuntimeError, 'already active'):
                await self.recorder.capture(1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            self.assertFalse(self.recorder.active)
            self.assertFalse(list(self.recorder.root.glob('*.pending.mkv')))
            self.recorder.worker = worker(self.root)
            self.assertTrue((await self.recorder.capture(1))['verified'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_waits_for_file_verification_before_discarding(self):
        entered, release = threading.Event(), threading.Event()
        original = self.recorder._verify

        def blocked(path, report):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test did not release verification')
            return original(path, report)

        with patch.object(self.recorder, '_verify', side_effect=blocked):
            task = asyncio.create_task(self.recorder.capture(1))
            try:
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(.005)
                task.cancel()
                await asyncio.sleep(.02)
                self.assertFalse(task.done())
                self.assertTrue(self.recorder.active)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(self.recorder.active)
                self.assertEqual(self.recorder.list()['recordings'], [])
                self.assertFalse(list(self.recorder.root.glob('*.pending.mkv')))
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    def test_identifier_and_symlink_guards_preserve_external_file(self):
        other = self.root / 'keep.txt'
        other.write_text('keep this')
        for ident in ('../keep', 'a' * 31, 'A' * 32, 42):
            with self.assertRaises(ValueError):
                self.recorder.delete(ident)
        self.recorder._root()
        ident = 'a' * 32
        link = self.recorder.root / (ident + '.mkv')
        link.symlink_to(other)
        with self.assertRaises(ValueError):
            self.recorder.delete(ident)
        with self.assertRaises(OSError):
            self.recorder._verify(link, REPORT)
        self.assertEqual(other.read_text(), 'keep this')
        self.assertEqual(self.recorder.list()['recordings'], [])


class RecordingCoreTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ToolCancellationTests.setUp
    asyncTearDown = fixtures.ToolCancellationTests.asyncTearDown
    connection = fixtures.ToolCancellationTests.connection
    send = fixtures.ToolCancellationTests.send
    reply = fixtures.ToolCancellationTests.reply

    async def test_screen_recording_still_requires_confirmation_in_relaxed_mode(self):
        self.core.config['security']['strict_confirmations'] = False
        self.core.screen_recorder.status = AsyncMock(side_effect=AssertionError('Unapproved capture'))
        result = await self.core.request_tool({'name': 'desktop.recording.capture', 'arguments': {'seconds': 1}}, 'fixture')
        self.assertEqual(result['status'], 'confirmation_required')
        self.core.config['carlos']['privacy_mode'] = 'GUEST'
        denied = await self.core.request_tool({'name': 'desktop.recording.capture', 'arguments': {'seconds': 1}}, 'guest')
        self.assertEqual(denied['status'], 'denied')

    async def test_stop_and_read_only_status_work_on_same_ipc_connection_as_capture(self):
        recorder = self.core.screen_recorder
        recorder.python = sys.executable
        recorder.worker = worker(self.root, blocked=True)
        spec = self.core.tools.get('desktop.recording.capture')
        task = asyncio.create_task(self.core.execute_tool(spec, {'seconds': 1}, 'fixture-capture'))
        try:
            async with asyncio.timeout(3):
                while not (self.root / 'worker.pid').exists():
                    await asyncio.sleep(.005)
            pid = int((self.root / 'worker.pid').read_text())
            await self.core.ipc.start()
            reader, writer = await self.connection()
            await self.send(writer, 'tool.call', 'status', {'name': 'desktop.recording.status', 'arguments': {}})
            status = await self.reply(reader, 'status')
            self.assertTrue(status['result']['active'])
            self.assertFalse(status['result']['capture_verified'])
            self.assertFalse(task.done())
            self.assertEqual(self.core.state.current, CoreState.USING_TOOL)
            await self.send(writer, 'command.submit', 'stop', {'text': 'stop', 'speak': False})
            await self.reply(reader, 'stop')
            result = await task
            self.assertEqual(result['status'], 'cancelled')
            self.assertFalse(list(recorder.root.glob('*.pending.mkv')))
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            await self.send(writer, 'health', 'health', {})
            self.assertTrue((await self.reply(reader, 'health'))['ok'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def test_recording_commands_are_exact_and_contracts_are_declared(self):
        self.assertEqual(direct_action('record my screen for 1 minute').arguments, {'seconds': 60})
        self.assertEqual(direct_action('record my screen for ten seconds').arguments, {'seconds': 10})
        self.assertEqual(direct_action('record desktop').tool, 'desktop.recording.capture')
        self.assertTrue(_is_responsive_request({'type': 'tool.call', 'payload': {'name': 'desktop.recording.status'}}))
        for name in ('status', 'capture', 'list', 'delete'):
            spec = self.core.tools.get('desktop.recording.' + name)
            self.assertEqual(spec.public()['contract_gaps'], [])
        self.assertFalse(self.core.tools.get('desktop.recording.delete').reversible)
