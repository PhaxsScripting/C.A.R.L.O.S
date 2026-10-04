import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

from ev.events import Event
from ev.paths import Paths
from ev.proactive import ProactiveSpeech
from ev.service import CoreService
from ev.state import CoreState


class ProactiveSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.core = CoreService(paths=Paths(*(root/name for name in ('config','data','state','cache','run'))))
        self.now, self.wall = 100., 1000.
        self.alerts = self.core.proactive = ProactiveSpeech(self.core, lambda:self.now, lambda:self.wall)
        self.core.config['personality']['proactive_speech_threshold'] = 'high'
        self.core.presence.state.update(session='UNLOCKED', observed_at=self.wall)
        self.core.voice.speak = AsyncMock(return_value={'status':'completed'})
        self.core.voice.stop_speaking = AsyncMock()
        self.available = patch.object(type(self.core.voice), 'tts_available', new_callable=PropertyMock, return_value=True)
        self.tts_available = self.available.start()
        self.sequence = 0

    async def asyncTearDown(self):
        self.available.stop()
        await self.core.voice.close()
        if hasattr(self.core.brain.provider,'close'):
            await self.core.brain.provider.close()
        self.core.daily.close()
        self.core.memory.close()
        self.core.task_journal.close()
        self.temporary.cleanup()

    def event(self, temperature=92, **changes):
        self.sequence += 1
        fields = dict(sequence=self.sequence, type='system.warning', source='telemetry',
                      payload={'kind':'thermal','celsius':temperature,'message':'private stuff'},
                      correlation_id='alert-fixture', monotonic=self.now)
        fields.update(changes)
        return Event(**fields)

    async def test_default_off_and_cli_ipc_setting_persists(self):
        self.core.config['personality'].pop('proactive_speech_threshold')
        self.assertFalse(self.alerts.offer(self.event()))
        self.assertEqual(self.alerts.snapshot()['threshold'],'off')
        await self.core.ipc.start()
        reader,writer = await asyncio.open_unix_connection(str(self.core.paths.socket))
        try:
            await reader.readline()
            writer.write((json.dumps({'id':'alert-setting','type':'personality.update',
                                     'payload':{'proactive_speech_threshold':'emergency'}})+'\n').encode())
            await writer.drain()
            async with asyncio.timeout(3):
                while True:
                    reply=json.loads(await reader.readline())
                    if reply.get('id')=='alert-setting':break
            self.assertTrue(reply['payload']['updated'])
            self.assertEqual(json.loads(self.core.paths.config_file.read_text())['personality']['proactive_speech_threshold'],'emergency')
            self.assertFalse(self.alerts.offer(self.event(92)))
            self.assertTrue(self.alerts.offer(self.event(99)))
            with self.assertRaises(ValueError):
                self.core.update_personality({'proactive_speech_threshold':'everything'})
        finally:
            writer.close()
            await writer.wait_closed()
            await self.core.ipc.stop()

    async def test_each_delivery_gate_discards_pending_without_speech(self):
        cases = [('privacy',None), ('transition',None), ('voice_privacy',None), ('gaming',None),
                 ('quiet',None), ('responses',None), ('tts',None), ('locked',None),
                 ('unknown',None), ('stale',None), ('future',None), ('stop',None)]
        for kind,_ in cases:
            with self.subTest(gate=kind):
                self.assertTrue(self.alerts.offer(self.event()))
                if kind=='privacy':self.core.config['carlos']['privacy_mode']='GUEST'
                elif kind=='transition':self.core.privacy.changing=True
                elif kind=='voice_privacy':self.core.voice.privacy_mode=True
                elif kind=='gaming':self.core.voice.gaming_suspended=True
                elif kind=='quiet':self.core.scenes.current['quiet']=True
                elif kind=='responses':self.core.config['assistant']['speak_responses']=False
                elif kind=='tts':self.tts_available.return_value=False
                elif kind=='locked':self.core.presence.state['session']='LOCKED'
                elif kind=='unknown':self.core.presence.state['session']='UNKNOWN'
                elif kind=='stale':self.core.presence.state['observed_at']=self.wall-11
                elif kind=='future':self.core.presence.state['observed_at']=self.wall+1
                elif kind=='stop':self.core.stop_event.set()
                self.assertFalse(await self.alerts.deliver_one())
                self.assertFalse(self.alerts.pending)
                self.core.config['carlos']['privacy_mode']='NORMAL'
                self.core.privacy.changing=False
                self.core.voice.privacy_mode=False
                self.core.voice.gaming_suspended=False
                self.core.scenes.current['quiet']=False
                self.core.config['assistant']['speak_responses']=True
                self.tts_available.return_value=True
                self.core.presence.state.update(session='UNLOCKED',observed_at=self.wall)
                self.core.stop_event.clear()
        self.core.voice.speak.assert_not_awaited()

    async def test_high_waits_through_interactive_states_and_never_opens_followup(self):
        self.assertTrue(self.alerts.offer(self.event()))
        for state in (CoreState.THINKING,CoreState.LISTENING,CoreState.USING_TOOL,CoreState.SPEAKING):
            self.core.state.current=state
            self.assertFalse(await self.alerts.deliver_one())
        self.core.state.current=CoreState.DORMANT
        for field in ('speech_pending','capture_active'):
            setattr(self.core.voice,field,True)
            self.assertFalse(await self.alerts.deliver_one())
            setattr(self.core.voice,field,False)
        self.assertTrue(await self.alerts.deliver_one())
        self.core.voice.stop_speaking.assert_not_awaited()
        call=self.core.voice.speak.await_args
        self.assertFalse(call.kwargs['allow_follow_up'])
        self.assertNotIn('private stuff',call.args[0])
        self.assertEqual(self.core.bus.history()[-1]['type'],'proactive.speech_completed')

    async def test_emergency_preempts_once_and_rechecks_after_await(self):
        self.core.state.current=CoreState.SPEAKING
        self.alerts.offer(self.event(99))
        self.assertFalse(await self.alerts.deliver_one())
        self.assertFalse(await self.alerts.deliver_one())
        self.core.voice.stop_speaking.assert_awaited_once_with('proactive_emergency')
        self.core.state.current=CoreState.DORMANT
        self.assertTrue(await self.alerts.deliver_one())
        self.now+=121
        self.alerts.offer(self.event(99))
        self.core.state.current=CoreState.SPEAKING
        async def revoke(reason):
            self.core._action_generation+=1
            self.core.state.current=CoreState.DORMANT
        self.core.voice.stop_speaking.side_effect=revoke
        self.assertFalse(await self.alerts.deliver_one())
        self.assertEqual(self.core.voice.speak.await_count,1)

    async def test_candidates_need_known_source_type_and_fresh_observation(self):
        for value in (True,False,None,'99',float('nan'),float('inf'),10**400,89,201):
            self.assertFalse(self.alerts.offer(self.event(value)))
        for event in (self.event(source='model'),self.event(private=True),self.event(payload=[]),
                      self.event(monotonic=89),self.event(monotonic=101),
                      self.event(type='download.completed'),self.event(type='coding.completed')):
            self.assertFalse(self.alerts.offer(event))
        data={'kind':'smart','severity':'HIGH','count':1,'observed_at':self.wall,'message':'private filename'}
        for key,value in (('count',True),('count',0),('observed_at',self.wall-31),('observed_at',self.wall+1),
                          ('severity','INFO'),('kind','invented')):
            self.assertFalse(self.alerts.offer(self.event(type='security.alert',source='security_monitor',payload={**data,key:value})))
        for kind in ('smart','services'):
            self.assertTrue(self.alerts.offer(self.event(type='security.alert',source='security_monitor',payload={**data,'kind':kind})))
        self.assertEqual(len(self.alerts.pending),2)
        await self.alerts.deliver_one()
        self.assertNotIn('private filename',self.core.voice.speak.await_args.args[0])

    async def test_coalescing_order_cooldown_and_emergency_upgrade(self):
        old=self.event()
        self.alerts.offer(old)
        for _ in range(50):self.alerts.offer(self.event())
        self.assertEqual(len(self.alerts.pending),1)
        self.assertFalse(self.alerts.offer(old))
        self.alerts.offer(self.event(99))
        self.assertFalse(self.alerts.offer(self.event(92)))
        await self.alerts.deliver_one()
        self.assertFalse(self.alerts.offer(self.event(99)))
        self.assertTrue(self.alerts.offer(self.event(92)))
        self.alerts.clear()
        self.now+=120
        self.core.presence.state['observed_at']=self.wall
        self.assertTrue(self.alerts.offer(self.event(99)))

    async def test_generation_threshold_and_control_events_drop_stale_work(self):
        self.alerts.offer(self.event())
        self.core._action_generation+=1
        self.assertFalse(await self.alerts.deliver_one())
        self.alerts.offer(self.event())
        self.core.config['personality']['proactive_speech_threshold']='emergency'
        self.assertFalse(await self.alerts.deliver_one())
        for kind in ('personality.updated','carlos.privacy_changed','carlos.privacy_transition','presence.session_changed',
                     'carlos.scene_changed','voice.conversation_ended','system.resume_observed','core.stopping'):
            self.alerts.offer(self.event(99))
            self.assertFalse(self.alerts.offer(self.event(type=kind)))
            self.assertFalse(self.alerts.pending)
        self.alerts.offer(self.event(99))
        self.assertFalse(self.alerts.offer(self.event(type='tts.interrupted',payload={'reason':'proactive_emergency'})))
        self.assertIn('thermal',self.alerts.pending)
        self.assertFalse(self.alerts.offer(self.event(type='tts.interrupted',payload={'reason':'user_request'})))
        self.assertFalse(self.alerts.pending)
        self.core.voice.speak.assert_not_awaited()

    async def test_policy_change_during_delivery_cancels_owned_speech(self):
        entered,cancelled=asyncio.Event(),asyncio.Event()
        async def ongoing(*args,**kwargs):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        self.core.voice.speak.side_effect=ongoing
        self.alerts.offer(self.event())
        delivery=asyncio.create_task(self.alerts.deliver_one())
        await asyncio.wait_for(entered.wait(),1)
        self.core.scenes.current['quiet']=True
        await asyncio.wait_for(delivery,1)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(any(event['type']=='proactive.speech_completed' for event in self.core.bus.history()))
        self.core.voice.stop_speaking.assert_not_awaited()

    async def test_backend_failure_gets_no_completed_receipt_or_retry(self):
        self.core.voice.speak.return_value={'status':'cancelled'}
        self.alerts.offer(self.event())
        await self.alerts.deliver_one()
        self.assertFalse(self.alerts.offer(self.event()))
        self.assertFalse(any(event['type']=='proactive.speech_completed' for event in self.core.bus.history()))
        self.now+=120
        self.core.voice.speak.side_effect=RuntimeError('output unavailable')
        self.alerts.offer(self.event())
        with self.assertRaises(RuntimeError):await self.alerts.deliver_one()
        self.assertFalse(self.alerts.pending)
        self.assertFalse(self.alerts.offer(self.event()))

    async def test_disabled_worker_sleeps_and_cancellation_reaps_owned_speech(self):
        self.core.config['personality']['proactive_speech_threshold']='off'
        with patch.object(self.alerts,'deliver_one', wraps=self.alerts.deliver_one) as deliver:
            worker=asyncio.create_task(self.alerts.run())
            await asyncio.sleep(.03)
            deliver.assert_not_called()
            worker.cancel()
            await asyncio.gather(worker,return_exceptions=True)
        self.core.config['personality']['proactive_speech_threshold']='high'
        entered,cancelled=asyncio.Event(),asyncio.Event()
        async def ongoing(*args,**kwargs):
            entered.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        self.core.voice.speak.side_effect=ongoing
        self.alerts.offer(self.event())
        worker=asyncio.create_task(self.alerts.run())
        await asyncio.wait_for(entered.wait(),1)
        worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(self.alerts.pending)

    async def test_alert_projection_failure_does_not_drop_timeline_event(self):
        self.core.config['memory']['activity_timeline']=True
        subscription,queue=self.core.bus.subscribe()
        with patch.object(self.alerts,'offer',side_effect=RuntimeError('projection failed')), \
                patch.object(self.core.timeline,'record',wraps=self.core.timeline.record) as record:
            worker=asyncio.create_task(self.core._persist_events(queue))
            try:
                self.core.bus.publish('system.warning','telemetry',{'kind':'thermal','celsius':92})
                await asyncio.wait_for(queue.join(),2)
                record.assert_called_once()
                self.assertEqual(self.core.memory.list_events(1)[0]["type"],"system.warning")
                self.assertFalse(worker.done())
            finally:
                worker.cancel()
                await asyncio.gather(worker,return_exceptions=True)
                self.core.bus.unsubscribe(subscription)

    async def test_real_persistence_subscription_routes_alert_and_clear_event(self):
        self.alerts.clock=time.monotonic
        self.core.presence.state['observed_at']=time.time()
        self.alerts.wall_clock=time.time
        subscription,queue=self.core.bus.subscribe()
        worker=asyncio.create_task(self.core._persist_events(queue))
        try:
            self.core.bus.publish('system.warning','telemetry',{'kind':'thermal','celsius':92})
            await asyncio.wait_for(queue.join(),2)
            self.assertIn('thermal',self.alerts.pending)
            self.core.bus.publish('personality.updated','settings',{})
            await asyncio.wait_for(queue.join(),2)
            self.assertFalse(self.alerts.pending)
        finally:
            worker.cancel()
            await asyncio.gather(worker,return_exceptions=True)
            self.core.bus.unsubscribe(subscription)


if __name__=='__main__':unittest.main()
