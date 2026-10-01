import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ev.memory.store import MemoryStore, SCHEMA
from ev.paths import Paths
from ev.service import CarlosCore
from ev.tools.base import ValidationError
from ev.tools.preferences import set_preference
from ev.tools.project_memory import search, remember, forget


class ProjectMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = CarlosCore(paths=Paths(*(self.root / name for name in
                                            ('config', 'data', 'state', 'cache', 'run'))))
        self.core.config['security']['allowed_roots'] = [str(self.root)]
        self.core.config['assistant']['speak_responses'] = False
        self.context = self.core.tools.context
        self.alpha, self.beta = self.root / 'project', self.root / 'project-other'
        self.alpha.mkdir(); self.beta.mkdir()

    def tearDown(self):
        self.core.daily.close()
        self.core.task_journal.close()
        self.core.memory.close()
        self.temp.cleanup()

    async def test_exact_project_scope_is_separate_from_global_and_other_projects(self):
        self.core.memory.remember('global_canary')
        await remember({'project': str(self.alpha), 'content': 'alpha_canary'}, self.context)
        await remember({'project': str(self.beta), 'content': 'beta_canary'}, self.context)
        result = await search({'project': str(self.alpha)}, self.context)
        self.assertIn('alpha_canary', str(result))
        self.assertNotIn('beta_canary', str(result))
        self.assertNotIn('global_canary', str(result))
        self.assertEqual(len(self.core.memory.list_memories()), 1)

    async def test_allowed_symlink_resolves_to_same_context_and_outside_is_refused(self):
        alias = self.root / 'alias'; alias.symlink_to(self.alpha, target_is_directory=True)
        await remember({'project': str(alias), 'content': 'canonical_canary'}, self.context)
        self.assertEqual(len((await search({'project': str(self.alpha)}, self.context))['memories']), 1)
        for path in ('/usr', str(self.root / 'missing')):
            with self.assertRaises(ValidationError):
                await search({'project': path}, self.context)

    async def test_wrong_project_cannot_delete_note_by_id(self):
        note = (await remember({'project': str(self.alpha), 'content': 'keep_canary'}, self.context))['memory']
        result = await forget({'project': str(self.beta), 'id': note['id']}, self.context)
        self.assertFalse(result['removed'])
        self.assertTrue(self.core.memory.project_memory_exists(str(self.alpha), note['id']))
        result = await forget({'project': str(self.alpha), 'id': note['id']}, self.context)
        self.assertTrue(result['removed'])

    async def test_save_and_delete_require_approval_through_real_executor(self):
        response = await self.core.request_tool({'name': 'memory.project.remember',
            'arguments': {'project': str(self.alpha), 'content': 'approved_canary'}}, 'fixture')
        self.assertEqual(response['status'], 'confirmation_required')
        self.assertEqual(self.core.memory.list_project_memories(str(self.alpha)), [])
        confirmation = response['confirmation']
        result = await self.core.resolve_confirmation({'id': confirmation['id'],
            'approval_token': confirmation['approval_token'], 'approved': True})
        self.assertEqual(result['status'], 'completed')
        note = result['result']['memory']
        response = await self.core.request_tool({'name': 'memory.project.forget',
            'arguments': {'project': str(self.alpha), 'id': note['id']}}, 'delete-fixture')
        self.assertEqual(response['status'], 'confirmation_required')
        self.assertTrue(self.core.memory.project_memory_exists(str(self.alpha), note['id']))

    async def test_private_notes_are_ram_only_and_never_return_to_disk(self):
        self.core.memory.set_private(True)
        result = await remember({'project': str(self.alpha), 'content': 'private_canary'}, self.context)
        self.assertEqual(result['storage'], 'RAM_ONLY')
        self.assertEqual(len((await search({'project': str(self.alpha)}, self.context))['memories']), 1)
        self.core.memory.set_private(False)
        self.assertEqual(self.core.memory.list_project_memories(str(self.alpha)), [])
        raw = b''.join(path.read_bytes() for path in self.core.paths.data_dir.glob('memory.db*'))
        self.assertNotIn(b'private_canary', raw)

    async def test_only_explicitly_selected_project_notes_enter_model_context(self):
        await remember({'project': str(self.alpha), 'content': 'alpha_context_canary'}, self.context)
        await remember({'project': str(self.beta), 'content': 'beta_context_canary'}, self.context)
        self.assertNotIn('context_canary', str(await self.core._model_context('compilers')))
        self.core.daily.save('preference', 'project', {'value': str(self.alpha)})
        self.assertNotIn('context_canary', str(await self.core._model_context('compilers')))
        await set_preference({'key': 'project', 'value': str(self.alpha)}, self.context)
        context = await self.core._model_context('compilers')
        self.assertIn('alpha_context_canary', str(context))
        self.assertNotIn('beta_context_canary', str(context))
        self.assertIn('not live evidence or permission to act', str(context))

    async def test_existing_database_upgrades_without_losing_notes_or_conversations(self):
        path = self.root / 'legacy.db'
        start = SCHEMA.index('CREATE TABLE IF NOT EXISTS project_memories')
        end = SCHEMA.index('CREATE TABLE IF NOT EXISTS conversations', start)
        legacy = sqlite3.connect(path)
        legacy.executescript(SCHEMA[:start] + SCHEMA[end:])
        legacy.execute("INSERT INTO memories VALUES(?,?,?,?,?)", ('legacy', 'legacy_global', '[]', 'before', 'before'))
        legacy.execute("INSERT INTO projects VALUES(?,?,?,?,?,?)", ('legacy_project', 'project', str(self.alpha), 'legacy_project_note', 'before', 'before'))
        legacy.execute("INSERT INTO conversations(correlation_id,role,content,created_at) VALUES(?,?,?,?)", ('legacy_session','user','legacy_conversation','before'))
        legacy.commit(); legacy.close()
        upgraded = MemoryStore(path)
        try:
            self.assertEqual(upgraded.list_memories()[0]['content'], 'legacy_global')
            self.assertEqual(upgraded.recent_conversation()[0]['content'], 'legacy_conversation')
            self.assertEqual(upgraded._connection.execute('SELECT notes FROM projects').fetchone()[0], 'legacy_project_note')
            note = upgraded.remember_project(str(self.alpha), 'new_note')
            self.assertTrue(upgraded.project_memory_exists(str(self.alpha), note['id']))
        finally:
            upgraded.close()

    async def test_reopen_preserves_global_and_project_memories(self):
        self.core.memory.remember('global_upgrade_canary')
        await remember({'project': str(self.alpha), 'content': 'project_upgrade_canary'}, self.context)
        reopened = MemoryStore(self.core.paths.database)
        try:
            self.assertEqual(len(reopened.list_memories()), 1)
            self.assertEqual(len(reopened.list_project_memories(str(self.alpha))), 1)
        finally:
            reopened.close()

    async def test_large_notes_are_bounded_before_ipc_serialization(self):
        for index in range(20):
            self.core.memory.remember_project(str(self.alpha), chr(0x1f30c) * 8000 + str(index))
        result = await search({'project': str(self.alpha)}, self.context)
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode('utf-8')), 410000)
        self.assertTrue(result['has_more'])

    async def test_guest_cannot_read_project_context_and_empty_note_is_refused(self):
        with self.assertRaises(ValidationError):
            await remember({'project': str(self.alpha), 'content': '   '}, self.context)
        self.core.config['carlos']['privacy_mode'] = 'GUEST'
        result = await self.core.request_tool({'name': 'memory.project.search',
            'arguments': {'project': str(self.alpha)}}, 'guest-fixture')
        self.assertEqual(result['status'], 'denied')
