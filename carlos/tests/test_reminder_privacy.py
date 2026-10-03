import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

from ev.paths import Paths
from ev.service import CoreService


class ReminderPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.core = CoreService(paths=Paths(*(root / name for name in
                                             ('config', 'data', 'state', 'cache', 'run'))))
        self.core.voice.speech_pending = True
        self.speak = AsyncMock()
        self.speaker_patch = patch.object(self.core.voice, 'speak', self.speak)
        self.speaker_patch.start()
        self.available_patch = patch.object(type(self.core.voice), 'tts_available',
                                            new_callable=PropertyMock, return_value=True)
        self.available_patch.start()
        self.subscriber, self.events = self.core.bus.subscribe()
        await self.core.ipc.start()
        self.reader, self.writer = await asyncio.open_unix_connection(str(self.core.paths.socket))
        await self.reader.readline()
        self.loop_task = None
        self.sequence = 0
        self.release = threading.Event()

    async def asyncTearDown(self):
        self.release.set()
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        self.writer.close()
        await self.writer.wait_closed()
        await self.core.ipc.stop()
        self.speaker_patch.stop()
        self.available_patch.stop()
        self.core.bus.unsubscribe(self.subscriber)
        await self.core.voice.close()
        if hasattr(self.core.brain.provider, 'close'):
            await self.core.brain.provider.close()
        self.core.daily.close()
        self.core.memory.close()
        self.core.task_journal.close()
        self.temporary.cleanup()

    async def request(self, kind, payload):
        import json
        self.sequence += 1
        identifier = 'owned-reminder-' + str(self.sequence)
        self.writer.write((json.dumps({'id': identifier, 'type': kind, 'payload': payload}) + '\n').encode())
        await self.writer.drain()
        async with asyncio.timeout(5):
            while True:
                response = json.loads(await self.reader.readline())
                if response.get('id') == identifier:
                    self.assertEqual(response['type'], 'response')
                    return response['payload']

    async def mode(self, mode):
        result = await self.request('carlos.privacy.set', {'mode': mode})
        self.assertEqual(result['mode'], mode)

    def add_due(self, label):
        saved = self.core.daily.add_reminder(label, 60)['reminder']
        with self.core.daily.connect() as db:
            db.execute('UPDATE reminders SET due=? WHERE id=?', (time.time() - 2, saved['id']))
        return saved['id']

    def start(self):
        self.loop_task = asyncio.create_task(self.core._reminder_loop())

    async def emitted(self, label):
        async with asyncio.timeout(4):
            while True:
                event = await self.events.get()
                if event.type == 'reminder.due' and event.payload['label'] == label:
                    return event

    async def fresh_delivery(self):
        self.core.voice.speech_pending = False
        self.add_due('Owned fresh reminder')
        await self.emitted('Owned fresh reminder')
        async with asyncio.timeout(3):
            while not self.speak.await_count:
                await asyncio.sleep(.02)
        self.assertEqual([call.args[0] for call in self.speak.await_args_list],
                         ['Reminder: Owned fresh reminder'])

    async def test_normal_due_reminder_delivers_once(self):
        identifier = self.add_due('Owned fresh reminder')
        self.start()
        self.core.voice.speech_pending = False
        await self.emitted('Owned fresh reminder')
        await asyncio.sleep(1.1)
        self.speak.assert_awaited_once()
        self.assertEqual(self.speak.await_args.args[0], 'Reminder: Owned fresh reminder')
        with self.core.daily.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM reminders WHERE id=?',
                                        (identifier,)).fetchone()[0], 'fired')

    async def test_queued_label_discarded_across_guest_and_back_to_normal(self):
        self.add_due('Owned old private reminder')
        self.start()
        await self.emitted('Owned old private reminder')
        await self.mode('GUEST')
        await self.mode('NORMAL')
        await self.fresh_delivery()

    async def test_queued_label_discarded_across_private_session_and_back(self):
        self.add_due('Owned old private reminder')
        self.start()
        await self.emitted('Owned old private reminder')
        await self.mode('PRIVATE SESSION')
        await self.mode('NORMAL')
        await self.fresh_delivery()

    async def test_guest_mode_does_not_claim_or_publish_personal_reminders(self):
        await self.mode('GUEST')
        self.add_due('Owned guest reminder')
        original = self.core.daily.due
        with patch.object(self.core.daily, 'due', wraps=original) as claim:
            self.start()
            await asyncio.sleep(1.1)
            claim.assert_not_called()
        self.assertFalse(any(row['type'] == 'reminder.due' for row in self.core.bus.history()))
        self.assertEqual(len(self.core.daily.reminders()), 1)
        self.speak.assert_not_awaited()

    async def test_worker_return_from_old_scope_not_published_after_privacy_change(self):
        identifier = self.add_due('Owned stale worker reminder')
        claimed = threading.Event()
        original = self.core.daily.due
        def delayed(now):
            rows = original(now)
            if any(row['id'] == identifier for row in rows):
                claimed.set()
                if not self.release.wait(5):
                    raise RuntimeError('Owned fixture did not release its worker')
            return rows
        with patch.object(self.core.daily, 'due', side_effect=delayed):
            self.start()
            self.assertTrue(await asyncio.to_thread(claimed.wait, 3))
            await self.mode('GUEST')
            self.release.set()
            await asyncio.sleep(.1)
            await self.mode('NORMAL')
            await self.fresh_delivery()
        labels = [row['payload']['label'] for row in self.core.bus.history()
                  if row['type'] == 'reminder.due']
        self.assertNotIn('Owned stale worker reminder', labels)

    async def test_explicit_stop_discards_waiting_reminder_speech(self):
        self.add_due('Owned stopped reminder')
        self.start()
        await self.emitted('Owned stopped reminder')
        await self.core._stop_all_actions('owned-reminder-stop')
        await self.fresh_delivery()


if __name__ == '__main__':
    unittest.main()
