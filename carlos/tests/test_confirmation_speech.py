import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from ev.paths import Paths
from ev.service import CarlosCore
from ev.voice.reply_stream import desktop_speech


class ConfirmationSpeechTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name)
        self.core=CarlosCore(paths=Paths(*(root/name for name in ('config','data','state','cache','run'))))
        self.addCleanup(self.core.memory.close)
        self.addCleanup(self.core.task_journal.close)
        self.addCleanup(self.core.daily.close)
        self.core._prepare_interactive_request=AsyncMock()
        self.core._schedule_response_speech=Mock()

    async def call(self,payload):
        return await self.core.handle_request({'type':'confirmation.respond','id':'owned-request','payload':payload})

    async def test_explicit_silent_confirmation_suppresses_streaming_and_finished_reply(self):
        seen=[]
        async def resolve(payload):
            seen.append(desktop_speech.get())
            self.assertIs(payload['speak'],False)
            return {'command':{'status':'completed','response':'Owned fixture response'}}
        self.core.resolve_confirmation=resolve
        result=await self.call({'speak':False})
        self.assertEqual(seen,[False])
        self.assertEqual(result['command']['status'],'completed')
        self.core._schedule_response_speech.assert_not_called()
        self.assertTrue(desktop_speech.get())

    async def test_desktop_confirmation_defaults_to_normal_reply_policy(self):
        seen=[]
        async def resolve(payload):
            seen.append(desktop_speech.get())
            return {'command':{'status':'completed','response':'Owned fixture response'}}
        self.core.resolve_confirmation=resolve
        await self.call({})
        self.assertEqual(seen,[True])
        self.core._schedule_response_speech.assert_called_once()

    async def test_permission_error_restores_the_callers_speech_scope(self):
        async def resolve(payload):
            self.assertFalse(desktop_speech.get())
            raise ValueError('Owned fixture invalid confirmation')
        self.core.resolve_confirmation=resolve
        with self.assertRaisesRegex(ValueError,'invalid confirmation'):
            await self.call({'speak':False})
        self.assertTrue(desktop_speech.get())
        self.core._schedule_response_speech.assert_not_called()

    async def test_silent_and_default_confirmations_do_not_share_scope(self):
        import asyncio
        silent_started,release=asyncio.Event(),asyncio.Event()
        seen=[]
        async def resolve(payload):
            if payload.get('speak') is False:
                silent_started.set()
                await release.wait()
            seen.append(desktop_speech.get())
            return {'status':'completed'}
        self.core.resolve_confirmation=resolve
        silent=asyncio.create_task(self.call({'speak':False}))
        await silent_started.wait()
        await self.call({})
        release.set()
        await silent
        self.assertEqual(seen,[True,False])
        self.assertTrue(desktop_speech.get())
        self.core._schedule_response_speech.assert_not_called()


if __name__=='__main__':
    unittest.main()
