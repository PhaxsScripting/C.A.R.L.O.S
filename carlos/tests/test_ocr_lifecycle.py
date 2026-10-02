import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from ev.process_runner import command
from ev.paths import Paths
from ev.service import CarlosCore
from ev.vision import ScreenPerception
from ev.tools.results import evaluate_result


class OcrLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.capture = 'a' * 32
        self.ready = self.root / 'worker.pid'
        self.worker = self.root / 'fixture-worker'
        self.worker.write_text('#!' + sys.executable + '\nimport os, time\nfrom pathlib import Path\n'
                               + 'Path(' + repr(str(self.ready)) + ').write_text(str(os.getpid()))\ntime.sleep(60)\n')
        self.worker.chmod(0o700)
        self.perception = ScreenPerception(self.root / 'captures', object(), {
            'ocr_python': str(self.worker), 'ocr_timeout_seconds': 2})
        Image.new('RGB', (120, 40), 'white').save(self.perception.capture_root / (self.capture + '.png'))
        self.perception.status = lambda: {'ocr': True}

    async def wait_for_worker(self):
        async with asyncio.timeout(2):
            while not self.ready.exists():
                await asyncio.sleep(.005)
        return int(self.ready.read_text())

    async def test_tool_cancellation_reaps_real_owned_worker_and_keeps_ipc_responsive(self):
        core = CarlosCore(paths=Paths(*(self.root / name for name in ('config', 'data', 'state', 'cache', 'run'))))
        core.tools.context.vision = self.perception
        reader = writer = None
        task = asyncio.create_task(core.tools.execute(core.tools.get('vision.ocr'), {'capture_id': self.capture}))
        try:
            pid = await self.wait_for_worker()
            await core.ipc.start()
            reader, writer = await asyncio.open_unix_connection(core.paths.socket)
            await reader.readline()
            writer.write(b'{"type":"health","id":"during-ocr","payload":{}}\n')
            await writer.drain()
            async with asyncio.timeout(1):
                while True:
                    reply = json.loads(await reader.readline())
                    if reply.get('id') == 'during-ocr':
                        break
            self.assertTrue(reply['payload']['ok'])
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                await self.perception.ocr(self.capture)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            self.assertFalse(self.perception._ocr_lock.locked())
            self.assertTrue((self.perception.capture_root / (self.capture + '.png')).exists())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if writer:
                writer.close()
                await writer.wait_closed()
            await core.ipc.stop()
            core.daily.close()
            core.memory.close()
            core.task_journal.close()

    async def test_timeout_reaps_worker_and_allows_next_request(self):
        self.perception.config['ocr_timeout_seconds'] = .1
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            await self.perception.ocr(self.capture)
        pid = await self.wait_for_worker()
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(self.perception._ocr_lock.locked())
        self.worker.write_text('#!' + sys.executable + '\nprint(\'EV_OCR_JSON:{"elements":[],"count":0,"text":"","duration_ms":0}\')\n')
        result = await self.perception.ocr(self.capture)
        self.assertTrue(result['verified'])
        self.assertFalse(result['scene_accuracy_verified'])
        self.assertFalse(result['coordinate_actions_allowed'])
        evidence = evaluate_result('vision.ocr', result)
        self.assertTrue(evidence.ok)
        self.assertFalse(evidence.verified)
        self.assertFalse(evidence.changed_state)
        self.assertEqual(evidence.scope, 'ocr_inference')

    async def test_failed_large_or_invalid_output_is_not_a_success(self):
        for code in ["print('x'*1100000)",
                     "print('EV_OCR_JSON:[]')",
                     "print('EV_OCR_JSON:{\"elements\":[{\"text\":\"fake\",\"confidence\":NaN}]}')",
                     "print('EV_OCR_JSON:{\"elements\":[],\"duration_ms\":Infinity}')",
                     "print('EV_OCR_JSON:{\"elements\":[]}'); raise SystemExit(2)"]:
            with self.subTest(code=code):
                self.worker.write_text('#!' + sys.executable + '\n' + code + '\n')
                with self.assertRaises(RuntimeError):
                    await self.perception.ocr(self.capture)
                self.assertFalse(self.perception._ocr_lock.locked())

    async def test_invalid_threshold_and_timeout_never_spawn_worker(self):
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            for score in (float('nan'), float('inf'), True, -1, 1.1):
                with self.subTest(score=score), self.assertRaises(ValueError):
                    await self.perception.ocr(self.capture, score)
            for timeout in (float('nan'), float('inf'), 0, 61):
                self.perception.config['ocr_timeout_seconds'] = timeout
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    await self.perception.ocr(self.capture)
            spawn.assert_not_awaited()

    async def test_cancel_during_creation_reaps_returned_child_even_with_second_cancel(self):
        create = asyncio.create_subprocess_exec
        entered, release = asyncio.Event(), asyncio.Event()
        child = None

        async def delayed(*args, **kwargs):
            nonlocal child
            child = await create(*args, **kwargs)
            entered.set()
            await release.wait()
            return child

        initial = asyncio.all_tasks()
        with patch('asyncio.create_subprocess_exec', side_effect=delayed):
            task = asyncio.create_task(command([sys.executable, '-c', 'import time; time.sleep(60)']))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                self.assertIsNotNone(child.returncode)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if child and child.returncode is None:
                    child.kill()
                    await child.wait()
        await asyncio.sleep(0)
        self.assertEqual(asyncio.all_tasks(), initial)

    async def test_cancelled_failed_spawn_remains_cancellation(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def failed(*args, **kwargs):
            entered.set()
            await release.wait()
            raise FileNotFoundError('fixture')

        with patch('asyncio.create_subprocess_exec', side_effect=failed):
            task = asyncio.create_task(command(['fixture']))
            await entered.wait()
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
