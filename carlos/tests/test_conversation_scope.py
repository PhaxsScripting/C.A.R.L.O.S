import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from ev.ai import ProviderTurn, ToolCall
from ev.memory.store import MemoryStore, SCHEMA
from ev.paths import Paths
from ev.service import CarlosCore
from ev.tools.project_memory import select_project


class ConversationScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = CarlosCore(paths=Paths(*(self.root / name for name in ('config','data','state','cache','run'))))
        self.core.config['security']['allowed_roots'] = [str(self.root)]
        self.core.config['assistant']['speak_responses'] = False
        self.alpha = self.root / 'alpha'; self.alpha.mkdir()
        self.beta = self.root / 'beta'; self.beta.mkdir()
        self.context = self.core.tools.context
        self.provider = self.core.brain.provider
        self.provider.begin = AsyncMock(return_value=ProviderTurn('fixture','fixture','Fixture reply'))

    def tearDown(self):
        self.core.daily.close(); self.core.task_journal.close(); self.core.memory.close()
        self.temp.cleanup()

    async def test_concurrent_requests_cannot_bypass_busy_guard_while_scope_is_read(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def scope():
            nonlocal calls
            calls += 1
            if calls == 2: entered.set()
            await release.wait()
            return str(self.alpha)
        async def run(*args, **kwargs):
            await asyncio.sleep(0.02)
            return {'status':'completed','response':'fixture'}
        self.core._project_scope = scope
        self.core._run_journaled_request = AsyncMock(side_effect=run)
        first = asyncio.create_task(self.core._submit_action_clauses('Explain compilers','one'))
        second = asyncio.create_task(self.core._submit_action_clauses('Explain compilers','two'))
        await asyncio.wait_for(entered.wait(),1)
        release.set()
        results = await asyncio.gather(first,second)
        self.assertEqual(sorted(r['status'] for r in results),['busy','completed'])
        self.core._run_journaled_request.assert_awaited_once()

    async def test_unavailable_scope_does_not_fall_back_to_general_history(self):
        self.core.memory.add_conversation('general','user','general_canary')
        async def stalled():
            await asyncio.sleep(2)
            return str(self.alpha)
        self.core.brain.project_provider = stalled
        with self.assertRaises(TimeoutError):
            await self.core.brain.submit('Explain compilers')
        self.provider.begin.assert_not_awaited()
        self.assertEqual(len(self.core.memory.recent_conversation()),1)
        self.assertIsNone(self.core.brain.context_project.get())

    async def test_model_receives_only_selected_project_conversation(self):
        self.core.memory.add_conversation('general', 'user', 'general_canary')
        self.core.memory.add_conversation('alpha', 'user', 'alpha_canary', project=str(self.alpha))
        self.core.memory.add_conversation('beta', 'user', 'beta_canary', project=str(self.beta))
        await select_project({'project':str(self.alpha)}, self.context)
        result = await self.core.brain.submit('Explain compilers')
        self.assertEqual(result['status'],'completed')
        history = self.provider.begin.await_args.args[1]
        self.assertIn('alpha_canary',str(history))
        self.assertNotIn('beta_canary',str(history))
        self.assertNotIn('general_canary',str(history))
        self.assertEqual(len(self.core.memory.recent_conversation(project=str(self.alpha))),3)
        listed = await self.core.handle_request({'type':'conversation.list','payload':{}})
        self.assertNotIn('beta_canary',str(listed))
        self.assertNotIn('general_canary',str(listed))

    async def test_general_context_is_explicit_and_does_not_delete_project_history(self):
        self.core.memory.add_conversation('alpha', 'user', 'alpha_canary', project=str(self.alpha))
        self.core.memory.add_conversation('general','user','general_canary')
        await select_project({'project':str(self.alpha)}, self.context)
        self.core.planner.last_entities['window'] = {'id':'stale-target'}
        result = await select_project({'project':''}, self.context)
        self.assertTrue(result['verified'])
        self.assertEqual(self.core.planner.last_entities,{})
        await self.core.brain.submit('Explain compilers')
        history = self.provider.begin.await_args.args[1]
        self.assertIn('general_canary',str(history))
        self.assertNotIn('alpha_canary',str(history))
        self.assertEqual(len(self.core.memory.recent_conversation(project=str(self.alpha))),1)

    async def test_reply_stays_in_original_project_when_preference_changes_mid_request(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def reply(*args):
            entered.set(); await finish.wait()
            return ProviderTurn('fixture','fixture','alpha_reply_canary')
        self.provider.begin = reply
        await select_project({'project':str(self.alpha)},self.context)
        pending = asyncio.create_task(self.core.brain.submit('Explain compilers'))
        await asyncio.wait_for(entered.wait(),1)
        await select_project({'project':str(self.beta)},self.context)
        finish.set(); await pending
        self.assertIn('alpha_reply_canary',str(self.core.memory.recent_conversation(project=str(self.alpha))))
        self.assertNotIn('alpha_reply_canary',str(self.core.memory.recent_conversation(project=str(self.beta))))
        self.assertIsNone(self.core.brain.context_project.get())

    async def test_context_hints_use_captured_scope_even_if_preference_changes(self):
        self.core.memory.remember_project(str(self.alpha),'alpha_note_canary')
        self.core.memory.remember_project(str(self.beta),'beta_note_canary')
        await select_project({'project':str(self.beta)},self.context)
        token = self.core.brain.context_project.set(str(self.alpha))
        try:
            context = await self.core._model_context('compilers')
            self.assertIn('alpha_note_canary',str(context))
            self.assertNotIn('beta_note_canary',str(context))
        finally:
            self.core.brain.context_project.reset(token)

    async def test_actual_pending_approval_completes_in_original_project(self):
        await select_project({'project':str(self.alpha)},self.context)
        self.provider.begin = AsyncMock(return_value=ProviderTurn('fixture','fixture','',[
            ToolCall('save-note','memory.project.remember',{'project':str(self.alpha),'content':'approved_note_canary'})]))
        self.provider.continue_with_tools = AsyncMock(return_value=ProviderTurn('fixture','fixture','Saved your project note.'))
        pending = await self.core.brain.submit('Remember this project note for alpha')
        self.assertEqual(pending['status'],'confirmation_required')
        await select_project({'project':str(self.beta)},self.context)
        confirmation = pending['confirmation']
        result = await self.core.resolve_confirmation({'id':confirmation['id'],
            'approval_token':confirmation['approval_token'],'approved':True})
        self.assertEqual(result['command']['conversation_project'],str(self.alpha))
        self.assertEqual(len(self.core.memory.list_project_memories(str(self.alpha))),1)
        self.assertEqual(self.core.memory.list_project_memories(str(self.beta)),[])
        self.assertIn(result['command']['response'],str(self.core.memory.recent_conversation(project=str(self.alpha))))
        self.assertEqual(self.core.memory.recent_conversation(project=str(self.beta)),[])

    async def test_confirmation_reply_inherits_original_receipt_scope(self):
        self.core.memory.add_conversation('pending','user','alpha_request',project=str(self.alpha))
        await select_project({'project':str(self.beta)},self.context)
        self.core.memory.add_conversation('pending','assistant','approved_alpha_reply')
        self.assertIn('approved_alpha_reply',str(self.core.memory.recent_conversation(project=str(self.alpha))))
        with self.assertRaises(ValueError):
            self.core.memory.add_conversation('pending','assistant','wrong_scope',project=str(self.beta))
        self.assertNotIn('wrong_scope',str(self.core.memory.recent_conversation()))

    async def test_invalid_or_unapproved_preference_does_not_select_project(self):
        self.core.daily.save('preference','project',{'value':str(self.alpha)})
        self.assertEqual(await self.core._project_scope(),'')
        await select_project({'project':str(self.alpha)},self.context)
        self.core.config['security']['allowed_roots'] = [str(self.beta)]
        self.assertEqual(await self.core._project_scope(),'')

    async def test_private_project_turns_never_copy_back_to_disk(self):
        self.core.memory.set_private(True)
        self.core.memory.add_conversation('private','user','private_scope_canary',project=str(self.alpha))
        self.assertEqual(len(self.core.memory.recent_conversation(project=str(self.alpha))),1)
        self.core.memory.set_private(False)
        self.assertEqual(self.core.memory.recent_conversation(project=str(self.alpha)),[])
        raw=b''.join(p.read_bytes() for p in self.core.paths.data_dir.glob('memory.db*'))
        self.assertNotIn(b'private_scope_canary',raw)

    async def test_legacy_conversations_remain_general_after_column_upgrade(self):
        path = self.root / 'legacy.db'
        legacy = sqlite3.connect(path)
        legacy.executescript(SCHEMA.replace("    project TEXT NOT NULL DEFAULT '',\n",''))
        legacy.execute('INSERT INTO conversations(correlation_id,role,content,created_at) VALUES(?,?,?,?)',('old','user','legacy_canary','before'))
        legacy.commit(); legacy.close()
        upgraded = MemoryStore(path)
        try:
            self.assertIn('legacy_canary',str(upgraded.recent_conversation(project='')))
            self.assertEqual(upgraded.recent_conversation(project=str(self.alpha)),[])
        finally:
            upgraded.close()
