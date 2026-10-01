import asyncio
import json
import logging
from pathlib import Path
import tempfile
import unittest

from ev.events import PhaxEventBus
from ev.ipc.server import IpcServer
from ev.ipc.protocol import encode_message


class PanelSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.private=False
        self.bus=PhaxEventBus()
        self.state='DORMANT'
        self.denied=False
        async def handler(request):
            self.assertEqual(request['type'],'panel.state')
            if self.denied:return {'status':'denied','error':'private_canary'}
            return {'privacy_mode':self.private,'state':self.state,'windows':['window_canary'],
                    'detail':'detail_canary','waveform':[.4], 'conversation':'conversation_canary'}
        self.server=IpcServer(Path(self.temp.name)/'ev.sock',self.bus,handler,logging.getLogger('panel-test'))
        await self.server.start()
        self.reader,self.writer=await asyncio.open_unix_connection(self.server.socket_path)
        await self.reader.readline()

    async def asyncTearDown(self):
        self.writer.close();await self.writer.wait_closed();await self.server.stop()
        self.temp.cleanup()

    async def send(self,kind,identifier='watch'):
        self.writer.write(encode_message({'type':kind,'id':identifier,'payload':{}}))
        await self.writer.drain()

    async def receive(self):
        return json.loads(await asyncio.wait_for(self.reader.readline(),1))

    async def subscribe(self):
        await self.send('panel.subscribe')
        messages=[await self.receive(),await self.receive()]
        self.assertEqual({m['type'] for m in messages},{'response','panel.state'})
        return next(m['payload'] for m in messages if m['type']=='panel.state')

    async def test_only_metadata_is_sent_and_privacy_changes_do_not_wait_for_poll(self):
        self.assertEqual(await self.subscribe(),{'privacy_mode':False,'state':'DORMANT'})
        self.private=True
        self.bus.publish('carlos.privacy_transition','privacy',{'changing':True,'secret':'event_canary'})
        result=await self.receive()
        self.assertEqual(result,{'type':'panel.state','payload':{'privacy_mode':True,'state':'DORMANT'}})
        self.private=False;self.state='THINKING'
        self.bus.publish('core.state_changed','core',{'request':'request_canary'})
        self.assertEqual((await self.receive())['payload'],{'privacy_mode':False,'state':'THINKING'})

    async def test_arbitrary_activity_and_transcripts_are_not_forwarded(self):
        await self.subscribe()
        self.bus.publish('voice.transcript','voice',{'text':'transcript_canary'})
        self.bus.publish('desktop.observed','desktop',{'windows':['window_canary']})
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.reader.readline(),.03)
        self.denied=True
        self.bus.publish('voice.privacy_changed','voice',{})
        result=await self.receive()
        self.assertEqual(result['payload'],{'privacy_mode':True,'state':'UNKNOWN'})

    async def test_duplicate_subscription_does_not_spawn_another_stream(self):
        await self.subscribe()
        await self.send('panel.subscribe','again')
        self.assertEqual((await self.receive())['id'],'again')
        self.private=True;self.bus.publish('carlos.privacy_changed','privacy',{})
        self.assertTrue((await self.receive())['payload']['privacy_mode'])
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.reader.readline(),.03)

    async def test_subscription_cannot_switch_to_raw_events(self):
        await self.subscribe()
        await self.send('subscribe')
        self.assertEqual((await self.receive())['type'],'error')
        self.bus.publish('voice.transcript','voice',{'text':'transcript_canary'})
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.reader.readline(),.03)

    async def test_disconnect_and_shutdown_reap_subscription(self):
        await self.subscribe()
        self.writer.close();await self.writer.wait_closed()
        for _ in range(50):
            if self.server.clients==0:break
            await asyncio.sleep(.002)
        self.assertEqual(self.server.clients,0)
        self.assertFalse(self.server._client_tasks)
        await self.server.stop()


class PrivacyTransitionStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_hides_before_slow_cancellation_and_recovers_failed_transition(self):
        from ev.paths import Paths
        from ev.service import CarlosCore
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            core=CarlosCore(paths=Paths(*(root/name for name in ('config','data','state','cache','run'))))
            entered,release=asyncio.Event(),asyncio.Event()
            async def blocked_stop(*args):
                entered.set();await release.wait();raise RuntimeError('controlled fixture failure')
            core._stop_all_actions=blocked_stop
            core.voice.set_privacy_mode=AsyncMock(return_value={})
            await core.ipc.start()
            reader,writer=await asyncio.open_unix_connection(core.paths.socket)
            await reader.readline()
            async def receive():
                return json.loads(await asyncio.wait_for(reader.readline(),1))
            try:
                writer.write(encode_message({'type':'panel.subscribe','id':'watch','payload':{}}));await writer.drain()
                initial=[await receive(),await receive()]
                self.assertFalse(next(m for m in initial if m['type']=='panel.state')['payload']['privacy_mode'])
                writer.write(encode_message({'type':'carlos.privacy.set','id':'private','payload':{'mode':'PRIVATE SESSION'}}));await writer.drain()
                await asyncio.wait_for(entered.wait(),1)
                changed=await receive()
                self.assertEqual(changed,{'type':'panel.state','payload':{'privacy_mode':True,'state':'DORMANT'}})
                self.assertEqual(core.privacy.mode,'NORMAL')
                release.set()
                replies=[await receive(),await receive()]
                self.assertFalse(next(m for m in replies if m['type']=='panel.state')['payload']['privacy_mode'])
                self.assertEqual(next(m for m in replies if m.get('id')=='private')['type'],'error')
                self.assertFalse(core.privacy.changing)
            finally:
                release.set();writer.close();await writer.wait_closed();await core.ipc.stop()
                core.daily.close();core.memory.close();core.task_journal.close()
