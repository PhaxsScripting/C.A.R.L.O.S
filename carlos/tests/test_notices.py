import asyncio
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ev.events import PhaxEventBus
from ev.notices import NoticeQueue
from ev.service import CarlosCore


class NoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bus = PhaxEventBus()

    def event(self, priority, **payload):
        kind, source = {'BACKGROUND': ('tool.completed', 'tools'),
                        'NORMAL': ('voice.full_test_complete', 'voice'),
                        'HIGH': ('system.error', 'system'),
                        'EMERGENCY': ('system.warning', 'telemetry')}[priority]
        if priority == 'EMERGENCY':
            payload.update(kind='thermal', celsius=99)
        return self.bus.publish(kind, source, payload)

    async def test_actual_ipc_and_persistence_progress_while_notifier_is_blocked(self):
        fixture = Path(__file__).with_name('fixtures') / 'notice_queue_live.py'
        result = await asyncio.to_thread(subprocess.run, [sys.executable, str(fixture)],
                                         capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['telemetry_progressed'])
        self.assertTrue(report['emergency_delivered_first_after_release'])
        self.assertEqual(report['actions_executed'], 0)

    async def test_security_changes_enter_the_notice_queue_without_blocking_ipc(self):
        fixture = Path(__file__).with_name('fixtures') / 'security_monitor_live.py'
        result = await asyncio.to_thread(subprocess.run, [sys.executable, str(fixture)],
                                        capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['security_notices_queued'], 6)
        self.assertEqual(report['high_events'], 2)
        self.assertEqual(report['emergency_events'], 0)
        self.assertFalse(report['monitor_tool_changed_state'])
        self.assertEqual(report['action_tasks_created'], 0)

    async def test_delivery_prioritizes_urgent_notices_and_preserves_fifo_within_rank(self):
        queue = NoticeQueue(5)
        background = self.event('BACKGROUND')
        high1, high2 = self.event('HIGH'), self.event('HIGH')
        normal, emergency = self.event('NORMAL'), self.event('EMERGENCY')
        for event in (background, high1, normal, high2, emergency):
            self.assertTrue(queue.offer(event))
        observed = [queue.get_nowait() for _ in range(5)]
        self.assertEqual(observed, [emergency, high1, high2, normal, background])
        for _ in observed:
            queue.task_done()
        await asyncio.wait_for(queue.join(), 1)

    async def test_security_notices_respect_quiet_scenes_and_require_monitor_source(self):
        process = SimpleNamespace(returncode=0, wait=AsyncMock())
        service = SimpleNamespace(config={'notifications': {'enabled': True}},
                                  scenes=SimpleNamespace(current={'quiet': True}), _notification_last={})
        info = self.bus.publish('security.observed', 'security_monitor', {'message': 'A startup file changed'})
        important = self.bus.publish('security.alert', 'security_monitor', {'message': 'SMART health failed'})
        fake = self.bus.publish('security.alert', 'language', {'message': 'Unverified model claim'})
        with patch('ev.service.os.path.isfile', return_value=True), patch('ev.service.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            await CarlosCore._notify_event(service, info)
            await CarlosCore._notify_event(service, fake)
            self.assertEqual(spawn.await_count, 0)
            await CarlosCore._notify_event(service, important)
            self.assertEqual(spawn.await_count, 1)
            service.scenes.current['quiet'] = False
            await CarlosCore._notify_event(service, info)
            self.assertEqual(spawn.await_count, 2)

    async def test_background_flood_cannot_discard_a_queued_emergency(self):
        queue = NoticeQueue(2)
        emergency = self.event('EMERGENCY')
        queue.offer(emergency)
        for i in range(100):
            queue.offer(self.event('BACKGROUND', private_canary=f'content-{i}'))
        self.assertIs(queue.get_nowait(), emergency)
        queue.task_done()
        latest = queue.get_nowait()
        self.assertEqual(latest.payload['private_canary'], 'content-99')
        queue.task_done()
        await asyncio.wait_for(queue.join(), 1)
        metrics = queue.metrics()
        self.assertEqual(metrics['dropped'], 99)
        self.assertEqual(metrics['dropped_by_priority']['EMERGENCY'], 0)
        self.assertNotIn('content-', json.dumps(metrics))
        metrics['dropped_by_priority']['HIGH'] = 100
        self.assertEqual(queue.metrics()['dropped_by_priority']['HIGH'], 0)

    async def test_lower_priority_is_rejected_when_all_queued_work_is_more_important(self):
        queue = NoticeQueue(1)
        high = self.event('HIGH')
        queue.offer(high)
        self.assertFalse(queue.offer(self.event('BACKGROUND')))
        self.assertIs(queue.get_nowait(), high)
        queue.task_done()
        await asyncio.wait_for(queue.join(), 1)

    async def test_replacement_keeps_join_pending_until_the_replacement_is_processed(self):
        queue = NoticeQueue(1)
        queue.offer(self.event('BACKGROUND'))
        joined = asyncio.create_task(queue.join())
        await asyncio.sleep(0)
        emergency = self.event('EMERGENCY')
        queue.offer(emergency)
        await asyncio.sleep(0)
        self.assertFalse(joined.done())
        self.assertIs(await queue.get(), emergency)
        await asyncio.sleep(0)
        self.assertFalse(joined.done())
        queue.task_done()
        await asyncio.wait_for(joined, 1)

    async def test_equal_priority_overflow_discards_oldest_and_remains_bounded(self):
        queue = NoticeQueue(2)
        events = [self.event('HIGH') for _ in range(100)]
        for event in events:
            queue.offer(event)
            self.assertLessEqual(queue.qsize(), 2)
        self.assertEqual([queue.get_nowait(), queue.get_nowait()], events[-2:])
        queue.task_done(); queue.task_done()
        await asyncio.wait_for(queue.join(), 1)

    async def test_waiting_consumer_is_woken_and_unknown_priority_is_normal(self):
        queue = NoticeQueue(1)
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        event = SimpleNamespace(priority='UNVERIFIED')
        queue.offer(event)
        self.assertIs(await asyncio.wait_for(waiter, 1), event)
        queue.task_done()
        queue.offer(SimpleNamespace(priority='UNVERIFIED'))
        queue.offer(self.event('EMERGENCY'))
        self.assertEqual(queue.metrics()['dropped_by_priority']['NORMAL'], 1)

    @unittest.skipUnless(hasattr(asyncio.Queue, 'shutdown'), 'Queue shutdown needs Python 3.13')
    async def test_shutdown_rejects_replacements_and_keeps_standard_drain_semantics(self):
        queue = NoticeQueue(1)
        queue.offer(self.event('HIGH'))
        queue.shutdown()
        with self.assertRaises(asyncio.QueueShutDown):
            queue.offer(self.event('EMERGENCY'))
        await queue.get(); queue.task_done()
        await asyncio.wait_for(queue.join(), 1)

    def test_unbounded_or_boolean_capacity_is_rejected(self):
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                NoticeQueue(value)

    async def test_thermal_escalation_bypasses_lower_priority_repeat_cooldown(self):
        process = SimpleNamespace(returncode=0, wait=AsyncMock())
        service = SimpleNamespace(config={'notifications': {'enabled':True, 'minimum_repeat_seconds':90}},
                                  scenes=SimpleNamespace(current={'quiet':False}), _notification_last={})
        warning = self.bus.publish('system.warning', 'telemetry', {'kind':'thermal', 'celsius':91})
        emergency = self.event('EMERGENCY')
        with patch('ev.service.os.path.isfile', return_value=True), patch('ev.service.time.monotonic', return_value=100), patch('ev.service.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            await CarlosCore._notify_event(service, warning)
            await CarlosCore._notify_event(service, warning)
            await CarlosCore._notify_event(service, emergency)
        self.assertEqual(spawn.await_count, 2)

    async def test_first_warning_after_boot_is_not_mistaken_for_a_repeat(self):
        process = SimpleNamespace(returncode=0, wait=AsyncMock())
        service = SimpleNamespace(config={'notifications': {'enabled':True, 'minimum_repeat_seconds':90}},
                                  scenes=SimpleNamespace(current={'quiet':False}), _notification_last={})
        warning = self.event('HIGH')
        with patch('ev.service.os.path.isfile', return_value=True), patch('ev.service.time.monotonic', return_value=10), patch('ev.service.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            await CarlosCore._notify_event(service, warning)
            await CarlosCore._notify_event(service, warning)
        self.assertEqual(spawn.await_count, 1)

    async def notification_body(self, message):
        process = SimpleNamespace(returncode=0, wait=AsyncMock())
        service = SimpleNamespace(config={'notifications': {'enabled':True}},
                                  scenes=SimpleNamespace(current={}), _notification_last={})
        event = self.event('HIGH', message=message)
        with patch('ev.service.os.path.isfile', return_value=True), patch('ev.service.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            await CarlosCore._notify_event(service, event)
        return spawn.await_args.args[-1]

    async def test_error_credentials_are_redacted_before_notification_arguments(self):
        body = await self.notification_body('request failed password="NOTICE-CREDENTIAL-CANARY" at worker startup')
        self.assertNotIn('NOTICE-CREDENTIAL-CANARY', body)
        self.assertIn('[REDACTED_CREDENTIAL]', body)
        self.assertIn('worker startup', body)

    async def test_complete_key_is_filtered_before_body_length_limit(self):
        private_key = '-----BEGIN PRIVATE KEY-----\n' + 'PRIVATE-NOTICE-CANARY-' * 60 + '\n-----END PRIVATE KEY-----'
        body = await self.notification_body(private_key + ' remaining error context')
        self.assertNotIn('PRIVATE-NOTICE-CANARY', body)
        self.assertIn('[REDACTED_PRIVATE_KEY]', body)
        self.assertIn('remaining error context', body)
        self.assertLessEqual(len(body), 500)

    async def test_untrusted_error_markup_is_literal_text(self):
        import xml.etree.ElementTree as XML
        body = await self.notification_body('build failed in a<b.cpp & <a href="https://example.com">link</a>')
        self.assertIn('a&lt;b.cpp &amp;', body)
        self.assertNotIn('<a ', body)
        self.assertIn('&lt;a href=', body)
        original = 'a' * 499 + '& trailing content'
        body = await self.notification_body(original)
        self.assertEqual(XML.fromstring('<body>' + body + '</body>').text, original[:500])
        body = await self.notification_body('error\x00\x1b in\ud800 worker')
        self.assertEqual(XML.fromstring('<body>' + body + '</body>').text, 'error in worker')

    async def test_support_exposes_only_queue_counts(self):
        from ev.paths import Paths
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core = CarlosCore(paths=Paths(*(root / n for n in ('config','data','state','cache','runtime'))))
            try:
                for i in range(40):
                    core._notification_queue.offer(self.event('BACKGROUND', message='PRIVATE-NOTICE-CANARY'))
                report = core.holosystem.support()
                self.assertEqual(report['notification_queue']['dropped'], 8)
                self.assertNotIn('PRIVATE-NOTICE-CANARY', json.dumps(report))
            finally:
                core.memory.close()
