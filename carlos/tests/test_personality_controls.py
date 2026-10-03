import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from ev.ai.local_llama import LocalLlamaProvider
from ev.paths import Paths
from ev.service import CoreService


class PersonalityControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.core = CoreService(paths=Paths(*(root/name for name in ('config','data','state','cache','run'))))

    async def asyncTearDown(self):
        await self.core.voice.close()
        if hasattr(self.core.brain.provider,'close'):
            await self.core.brain.provider.close()
        self.core.daily.close()
        self.core.memory.close()
        self.core.task_journal.close()
        self.temporary.cleanup()

    async def test_actual_ipc_changes_persist_and_update_delivery_without_audio(self):
        await self.core.ipc.start()
        reader,writer = await asyncio.open_unix_connection(str(self.core.paths.socket))
        try:
            await reader.readline()
            payload={'humor':'off','sarcasm':'light','name_usage':'often','speaking_rate':1.3}
            writer.write((json.dumps({'id':'delivery','type':'personality.update','payload':payload})+'\n').encode())
            await writer.drain()
            async with asyncio.timeout(3):
                while True:
                    response=json.loads(await reader.readline())
                    if response.get('id')=='delivery':break
            self.assertEqual(response['type'],'response')
            saved=json.loads(self.core.paths.config_file.read_text())
            for key,value in payload.items():
                self.assertEqual(saved['personality'][key],value)
                self.assertEqual(response['payload']['personality'][key],value)
            self.assertEqual(saved['voice']['tts']['speaking_rate'],1.3)
            self.assertEqual(self.core.voice.tts.config['speaking_rate'],1.3)
            self.assertEqual(self.core.paths.config_file.stat().st_mode & 0o777,0o600)
            self.assertIsNone(getattr(self.core.voice.tts,'_worker',None))
        finally:
            writer.close()
            await writer.wait_closed()
            await self.core.ipc.stop()

    async def test_other_personality_changes_preserve_the_actual_existing_voice_rate(self):
        self.core.config['voice']['tts']['speaking_rate']=1.18
        self.core.config['personality']['speaking_rate']=0.85
        result=self.core.update_personality({'humor':'normal'})
        self.assertEqual(result['personality']['speaking_rate'],1.18)
        self.assertEqual(self.core.voice.tts.config['speaking_rate'],1.18)
        self.assertEqual(self.core.snapshot()['personality']['speaking_rate'],1.18)

    async def test_invalid_mixed_update_changes_no_settings_or_events(self):
        before=self.core.paths.config_file.read_bytes()
        runtime=copy.deepcopy(self.core.config)
        events=len(self.core.bus.history())
        for value in (True,False,0.64,1.51,float('nan'),float('inf'),'1.2'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.core.update_personality({'humor':'off','speaking_rate':value})
                self.assertEqual(self.core.paths.config_file.read_bytes(),before)
                self.assertEqual(self.core.config,runtime)
                self.assertEqual(len(self.core.bus.history()),events)
        for payload in ({},{'sarcasm':'mean'},{'name_usage':'constantly'},{'humor':'anything'}, {'unknown':True}):
            with self.assertRaises(ValueError):self.core.update_personality(payload)
            self.assertEqual(self.core.paths.config_file.read_bytes(),before)

    async def test_private_guest_and_transition_updates_do_not_persist(self):
        for mode in ('PRIVATE SESSION','GUEST','NORMAL'):
            self.core.config['carlos']['privacy_mode']=mode
            self.core.privacy.changing=mode=='NORMAL'
            before=self.core.paths.config_file.read_bytes()
            runtime=copy.deepcopy(self.core.config)
            with self.assertRaisesRegex(ValueError,'Persistent personality'):
                self.core.update_personality({'tone':'calm','voice_expressiveness':0.8})
            self.assertEqual(self.core.paths.config_file.read_bytes(),before)
            self.assertEqual(self.core.config,runtime)
        self.core.privacy.changing=False

    async def test_range_endpoints_and_normalized_choices_apply(self):
        for speed in (0.65,1.5):
            result=self.core.update_personality({'speaking_rate':speed,'humor':'OFF','sarcasm':'LIGHT'})
            self.assertEqual(result['personality']['speaking_rate'],speed)
            self.assertEqual(result['personality']['humor'],'off')
            self.assertEqual(self.core.voice.tts.config['speaking_rate'],speed)


class PersonalityPromptTests(unittest.TestCase):
    def test_full_and_compact_prompts_keep_delivery_and_action_truthfulness(self):
        for compact in (True,False):
            provider=LocalLlamaProvider({'model':'fixture','compact_prompt':compact},
                                        {'humor':'off','sarcasm':'light','name_usage':'rare','working_verbosity':'silent'})
            prompt=provider._system_instructions()
            self.assertIn('Skip jokes',prompt)
            self.assertIn('never mock the user',prompt)
            self.assertIn('do not invent a name',prompt)
            self.assertIn('Skip progress chatter',prompt)
            self.assertIn('tool',prompt)
            provider.set_personality({'humor':'normal','sarcasm':'off','name_usage':'often','working_verbosity':'conversational'})
            prompt=provider._system_instructions()
            self.assertIn('occasional conversational humor',prompt)
            self.assertIn('Keep sarcasm off',prompt)
            self.assertIn('do not narrate every tool field',prompt)


if __name__=='__main__':
    unittest.main()
