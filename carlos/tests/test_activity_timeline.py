import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from ev.paths import Paths
from ev.service import CarlosCore
from ev.timeline import ActivityTimeline
from ev.tools.base import ValidationError


class TimelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.core = CarlosCore(paths=Paths(*(root / name for name in
                                            ('config', 'data', 'state', 'cache', 'run'))))

    def tearDown(self):
        self.core.daily.close()
        self.core.task_journal.close()
        self.core.memory.close()
        self.temp.cleanup()

    async def test_opt_in_changes_real_event_persistence_but_not_live_delivery(self):
        subscriber, queue = self.core.bus.subscribe()
        worker = asyncio.create_task(self.core._persist_events(queue))
        try:
            self.core.bus.publish('core.started', 'core', {'pid': 123})
            await asyncio.wait_for(queue.join(), 2)
            self.assertEqual(self.core.memory.list_events(), [])
            self.assertTrue(self.core.bus.history())
            result = await self.core.settings_center.update(
                {'key': 'activity_timeline', 'value': True}, None)
            self.assertTrue(result['verified'])
            self.core.bus.publish('coding.started', 'coding_agent', {'project': 'fixture'})
            await asyncio.wait_for(queue.join(), 2)
            self.assertEqual(self.core.memory.list_events()[0]['type'], 'coding.started')
            await self.core.settings_center.update({'key': 'activity_timeline', 'value': False}, None)
            self.core.bus.publish('system.error', 'test', {'message': 'fixture'})
            await asyncio.wait_for(queue.join(), 2)
            self.assertEqual(len(self.core.memory.list_events()), 1)
            data = json.loads(self.core.paths.config_file.read_text())
            self.assertFalse(data['memory']['activity_timeline'])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            self.core.bus.unsubscribe(subscriber)

    def test_metadata_excludes_task_content_audio_screens_and_credentials(self):
        self.core.config['memory']['activity_timeline'] = True
        event = self.core.bus.publish('coding.completed', 'coding_agent', {
            'status': 'COMPLETED', 'reason': 'token=credential_canary',
            'content': 'content_canary', 'screen_image': 'image_canary',
            'audio': 'audio_canary', 'stdout': 'stdout_canary',
            'form_fields': 'form_canary', 'project': 'fixture'})
        self.core.timeline.record(event)
        rows = self.core.timeline.read({}, None)['events']
        raw = str(rows)
        for canary in ('credential_canary', 'content_canary', 'image_canary',
                       'audio_canary', 'stdout_canary', 'form_canary'):
            self.assertNotIn(canary, raw)
        self.assertEqual(rows[0]['payload']['project'], 'fixture')

    def test_private_and_transient_events_never_reach_disk(self):
        self.core.config['memory']['activity_timeline'] = True
        self.core.bus.private = True
        self.core.timeline.record(self.core.bus.publish('core.started', 'fixture'))
        self.core.bus.private = False
        self.core.timeline.record(self.core.bus.publish('voice.audio_level', 'fixture', {'rms': .4}))
        self.assertEqual(self.core.memory.list_events(), [])

    def test_retention_keeps_newest_events_and_query_is_exact(self):
        for sequence, kind in enumerate(('core.started', 'tool.failed', 'tool.completed'), 1):
            row = self.core.bus.publish(kind, 'fixture').as_dict()
            self.core.memory.record_event(row, limit=2)
        rows = self.core.memory.list_events()
        self.assertEqual([row['type'] for row in rows], ['tool.completed', 'tool.failed'])
        self.assertEqual(len(self.core.memory.list_events(event_type='tool.failed')), 1)
        self.assertEqual(self.core.memory.list_events(event_type="' OR 1=1 --"), [])

    def test_clear_requires_confirmation_and_keeps_other_memory_tiers(self):
        spec = self.core.tools.get('memory.timeline.clear')
        self.assertTrue(spec.requires_confirmation)
        self.core.memory.remember('explicit fixture memory')
        self.core.memory.add_conversation('fixture', 'user', 'conversation fixture')
        self.core.config['memory']['activity_timeline'] = True
        self.core.timeline.record(self.core.bus.publish('core.started', 'fixture'))
        result = self.core.timeline.clear({}, None)
        self.assertEqual(result['removed_events'], 1)
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.core.memory.list_memories()), 1)
        self.assertEqual(len(self.core.memory.recent_conversation()), 1)

    async def test_guest_cannot_read_history_and_clear_is_not_implicitly_approved(self):
        result = await self.core.request_tool({'name': 'memory.timeline.clear', 'arguments': {}}, 'fixture')
        self.assertEqual(result['status'], 'confirmation_required')
        self.core.config['carlos']['privacy_mode'] = 'GUEST'
        result = await self.core.request_tool({'name': 'memory.timeline', 'arguments': {}}, 'fixture')
        self.assertEqual(result['status'], 'denied')

    def test_old_rows_are_sanitized_on_read_without_rewriting_history(self):
        row = self.core.bus.publish('core.started', 'fixture').as_dict()
        row['payload'] = {'content': 'legacy_canary', 'status': 'READY'}
        self.core.memory.record_event(row)
        visible = self.core.timeline.read({}, None)['events']
        self.assertEqual(visible[0]['payload'], {'status': 'READY'})
        self.assertEqual(self.core.memory.list_events()[0]['payload']['content'], 'legacy_canary')

    def test_private_clear_never_claims_to_erase_saved_disk_history(self):
        self.core.config['memory']['activity_timeline'] = True
        self.core.timeline.record(self.core.bus.publish('core.started', 'fixture'))
        self.core.config['carlos']['privacy_mode'] = 'PRIVATE SESSION'
        self.core.privacy.apply_storage()
        with self.assertRaises(ValidationError):
            self.core.timeline.clear({}, None)
        self.assertEqual(len(self.core.memory.list_events()), 1)

    def test_clear_does_not_allow_old_queued_events_to_reappear(self):
        self.core.config['memory']['activity_timeline'] = True
        queued = self.core.bus.publish('core.started', 'fixture')
        self.core.timeline.clear({}, None)
        self.core.timeline.record(queued)
        self.assertEqual(self.core.memory.list_events(), [])
        later = self.core.bus.publish('core.stopping', 'fixture')
        self.core.timeline.record(later)
        self.assertEqual(self.core.memory.list_events()[0]['type'], 'core.stopping')
