import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

from ev.coding_wait import waiting_summary
from ev.events import Event
from ev.paths import Paths
from ev.service import CoreService
from ev.proactive import ProactiveSpeech
from ev.state import CoreState


def proposal(identifier='a'*32):
    return {'proposal_id': identifier, 'status': 'READY_FOR_REVIEW', 'approval_required': True,
            'executed': False, 'checkpoint_possible': True, 'agent': {'available': True}, 'created_epoch': 1000.,
            'request': 'private request', 'project': '/private/project', 'diagnostics': 'private failure'}


class CodingWaitSummaryTests(unittest.TestCase):
    def test_only_explicit_ready_review_or_validated_deployment_is_waiting(self):
        ready = proposal()
        result = waiting_summary(ready)
        self.assertEqual(result['phase'], 'REVIEW_PROPOSAL')
        self.assertNotIn('private', str(result))
        deployed = {**ready, 'status': 'VALIDATED_AWAITING_DEPLOYMENT_REVIEW', 'executed': True,
                    'deployment_approved': False, 'commit_id': 'f'*40, 'tests': [{'passed': True}], 'completed_epoch': 1001.}
        self.assertEqual(waiting_summary(deployed)['phase'], 'REVIEW_DEPLOYMENT')
        for field, value in (('status','VALIDATION_FAILED'),('executed',False),('deployment_approved',True),
                             ('commit_id','missing'),('tests',[]),('tests',[{'passed':1}])):
            self.assertIsNone(waiting_summary({**deployed,field:value}))

    def test_invalid_types_missing_authentication_and_clocks_never_grant_waiting(self):
        ready = proposal()
        for field, value in (('status','BLOCKED'),('status','RUNNING'),('proposal_id','bad'),('executed',0),
                             ('checkpoint_possible',1),('agent',{'available':False}),('approval_required',1),
                             ('created_epoch',True),('created_epoch',float('nan')),('created_epoch',float('inf')),
                             ('created_epoch',10**400),('created_epoch',0)):
            self.assertIsNone(waiting_summary({**ready,field:value}))
        self.assertIsNone(waiting_summary(None))


class CodingWaitSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(); root = Path(self.temporary.name)
        self.core = CoreService(paths=Paths(*(root/name for name in ('config','data','state','cache','run'))))
        self.now, self.wall = 100., 1000.
        self.alerts = self.core.proactive = ProactiveSpeech(self.core,lambda:self.now,lambda:self.wall)
        self.core.config['personality']['proactive_speech_threshold'] = 'high'
        self.core.presence.state.update(session='UNLOCKED',observed_at=self.wall)
        self.core.coding_agent._proposals['a'*32] = proposal()
        self.core.voice.speak = AsyncMock(return_value={'status':'completed'})
        self.core.voice.stop_speaking = AsyncMock()
        self.available = patch.object(type(self.core.voice),'tts_available',new_callable=PropertyMock,return_value=True)
        self.available.start()
        self.sequence = 0

    async def asyncTearDown(self):
        self.available.stop(); await self.core.voice.close()
        if hasattr(self.core.brain.provider,'close'):await self.core.brain.provider.close()
        self.core.daily.close(); self.core.memory.close(); self.core.task_journal.close(); self.temporary.cleanup()

    def event(self, **changes):
        self.sequence += 1
        fields = {'sequence':self.sequence,'type':'coding.waiting','source':'coding_agent',
                  'payload':waiting_summary(self.core.coding_agent._proposals['a'*32]),
                  'monotonic':self.now,'correlation_id':'a'*32}
        fields.update(changes)
        return Event(**fields)

    async def test_fresh_owned_waiting_is_high_and_uses_fixed_private_free_text(self):
        event = self.event()
        self.assertEqual(event.priority,'HIGH')
        self.assertTrue(self.alerts.offer(event))
        self.core.state.current = CoreState.USING_TOOL
        self.assertFalse(await self.alerts.deliver_one())
        self.core.state.current = CoreState.DORMANT
        self.assertTrue(await self.alerts.deliver_one())
        call = self.core.voice.speak.await_args
        self.assertIn('review',call.args[0])
        self.assertNotIn('private',call.args[0]); self.assertNotIn('/private/project',call.args[0])
        self.assertFalse(call.kwargs['allow_follow_up'])
        self.core.voice.stop_speaking.assert_not_awaited()

    async def test_spoofed_private_stale_or_unknown_jobs_never_queue(self):
        payload = self.event().payload
        for changes in ({'source':'model'},{'private':True},{'monotonic':89},
                        {'payload':{**payload,'proposal_id':'b'*32}},
                        {'payload':{**payload,'proposal_id':[]}},
                        {'payload':{**payload,'observed_at':969}},
                        {'payload':{**payload,'observed_at':1001}},
                        {'payload':{**payload,'approval_required':1}},
                        {'payload':{**payload,'request':'private text'}}):
            self.assertFalse(self.alerts.offer(self.event(**changes)))
        self.assertFalse(self.alerts.pending)

    async def test_started_cancelled_failed_and_changed_checkpoints_invalidate_pending_review(self):
        for status in ('RUNNING','CANCELLED','FAILED','BLOCKED'):
            self.core.coding_agent._proposals['a'*32] = proposal()
            self.assertTrue(self.alerts.offer(self.event()))
            self.core.coding_agent._proposals['a'*32]['status'] = status
            self.assertFalse(await self.alerts.deliver_one())
            self.assertFalse(self.alerts.pending)
        self.core.coding_agent._proposals['a'*32] = proposal()
        self.assertTrue(self.alerts.offer(self.event()))
        self.core.coding_agent._proposals['a'*32]['checkpoint_possible'] = False
        self.assertFalse(await self.alerts.deliver_one())
        self.core.voice.speak.assert_not_awaited()

    async def test_privacy_emergency_only_and_cooldown_preserve_silence(self):
        self.core.config['carlos']['privacy_mode'] = 'LOCAL ONLY'
        self.assertFalse(self.alerts.offer(self.event()))
        self.core.config['carlos']['privacy_mode'] = 'NORMAL'
        self.core.config['personality']['proactive_speech_threshold'] = 'emergency'
        self.assertFalse(self.alerts.offer(self.event()))
        self.core.config['personality']['proactive_speech_threshold'] = 'high'
        self.assertTrue(self.alerts.offer(self.event()))
        await self.alerts.deliver_one()
        self.assertFalse(self.alerts.offer(self.event()))
        self.now += 121; self.wall += 121
        self.core.presence.state['observed_at'] = self.wall
        self.core.coding_agent._proposals['a'*32]['created_epoch'] = self.wall
        self.assertTrue(self.alerts.offer(self.event()))

    async def test_review_approved_during_owned_synthesis_cancels_and_joins_speech(self):
        entered = asyncio.Event(); finished = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            try:await asyncio.Event().wait()
            finally:finished.set()
        self.core.voice.speak.side_effect = blocked
        self.assertTrue(self.alerts.offer(self.event()))
        task = asyncio.create_task(self.alerts.deliver_one())
        await entered.wait()
        self.core.coding_agent._proposals['a'*32]['status'] = 'RUNNING'
        await asyncio.wait_for(task,2)
        self.assertTrue(finished.is_set())
        self.assertFalse(any(event['type']=='proactive.speech_completed' for event in self.core.bus.history()))

    async def test_direct_and_tool_proposal_routes_refuse_private_scopes_before_probe_or_save(self):
        from ev.tools.builtin import coding_agent_proposal
        for mode in ('LOCAL ONLY','PRIVATE SESSION','GUEST'):
            self.core.config['carlos']['privacy_mode'] = mode
            with patch.object(self.core.coding_agent,'inspect_proposal') as probe, patch.object(self.core.coding_agent,'record_proposal') as save:
                for route in ('direct','tool'):
                    if route == 'direct':
                        result = await self.core.handle_request({'type':'coding.propose','payload':{'request':'owned','project':'owned'}})
                    else:
                        result = await coding_agent_proposal({'request':'owned','project':'owned'},self.core.tools.context)
                    self.assertEqual(result['status'],'denied')
                probe.assert_not_called(); save.assert_not_called()

    async def test_scope_change_during_read_only_inspection_never_persists_or_emits_proposal(self):
        for change in ('privacy','transition','generation','stop','stopping_tools'):
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            def inspect(*args):
                entered.set()
                try:
                    if not release.wait(2):raise AssertionError('Fixture was not released')
                    return proposal('b'*32)
                finally:finished.set()
            with patch.object(self.core.coding_agent,'inspect_proposal',side_effect=inspect), patch.object(self.core.coding_agent,'record_proposal') as save:
                task = asyncio.create_task(self.core.prepare_coding_proposal({'request':'owned','project':'owned'}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,2))
                    if change == 'privacy':self.core.config['carlos']['privacy_mode'] = 'PRIVATE SESSION'
                    elif change == 'transition':self.core.privacy.changing = True
                    elif change == 'generation':self.core._action_generation += 1
                    elif change == 'stop':self.core.stop_event.set()
                    else:self.core._stopping_tools = True
                finally:release.set()
                result = await asyncio.wait_for(task,2)
                self.assertEqual(result['status'],'denied'); save.assert_not_called()
            self.assertTrue(finished.is_set())
            self.core.config['carlos']['privacy_mode'] = 'NORMAL'; self.core.privacy.changing = False
            self.core.stop_event.clear(); self.core._stopping_tools = False
        self.assertNotIn('b'*32,self.core.coding_agent._proposals)

    async def test_cancelled_inspection_cannot_save_when_the_bounded_read_worker_finishes(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def inspect(*args):
            entered.set()
            try:
                if not release.wait(2):raise AssertionError('Fixture was not released')
                return proposal('b'*32)
            finally:finished.set()
        with patch.object(self.core.coding_agent,'inspect_proposal',side_effect=inspect), patch.object(self.core.coding_agent,'record_proposal') as save:
            task = asyncio.create_task(self.core.prepare_coding_proposal({'request':'owned','project':'owned'}))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            finally:release.set()
            self.assertTrue(await asyncio.to_thread(finished.wait,2))
            save.assert_not_called()
        self.assertNotIn('b'*32,self.core.coding_agent._proposals)
