import asyncio
import copy
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.events import EventBus
from ev.memory.store import MemoryStore
from ev.permissions import Permission
from ev.tools import ToolContext, ToolRegistry, ValidationError
from ev.tools.project_memory import register_project_memory
from ev.tools.builtin import register_builtin_tools
from ev.tools.results import evaluate_result


class ProjectMemoryReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'; self.project.mkdir()
        self.memory = MemoryStore(self.root / 'memory.db')
        self.context = ToolContext({'security': {'allowed_roots': [str(self.root)], 'max_tool_output_bytes': 512000}},
                                   EventBus(), logging.getLogger('project-memory-readback'), memory=self.memory)
        self.registry = ToolRegistry(self.context)
        register_project_memory(self.registry)

    async def asyncTearDown(self):
        self.memory.close(); self.temporary.cleanup()

    async def execute(self, operation, **args):
        return await self.registry.execute(self.registry.get('memory.project.' + operation),
                                           {'project': str(self.project), **args})

    async def test_actual_save_and_forget_have_exact_committed_evidence(self):
        saved = await self.execute('remember', content='  owned note  ', tags=['B', ' a ', 'b'])
        self.assertEqual(saved['memory']['content'], 'owned note')
        self.assertEqual(saved['memory']['tags'], ['a', 'b'])
        reopened = MemoryStore(self.memory.path)
        try:
            self.assertEqual(reopened.project_memory(str(self.project), saved['memory']['id']), saved['memory'])
            receipt = evaluate_result('memory.project.remember', saved)
            self.assertTrue(receipt.verified); self.assertTrue(receipt.changed_state)
            deleted = await self.execute('forget', id=saved['memory']['id'])
            self.assertIsNone(reopened.project_memory(str(self.project), saved['memory']['id']))
            self.assertTrue(evaluate_result('memory.project.forget', deleted).changed_state)
            missing = await self.execute('forget', id=saved['memory']['id'])
            self.assertFalse(missing['removed'])
            self.assertFalse(evaluate_result('memory.project.forget', missing).changed_state)
        finally:
            reopened.close()

    async def test_sqlite_trigger_corruption_refuses_success_without_replay_or_rollback(self):
        self.memory._connection.execute("CREATE TRIGGER corrupt_note AFTER INSERT ON project_memories "
                                        "BEGIN UPDATE project_memories SET content='later value' WHERE id=NEW.id; END")
        with patch.object(self.memory, 'remember_project', wraps=self.memory.remember_project) as write:
            with self.assertRaises(RuntimeError):
                await self.execute('remember', content='expected value')
            write.assert_called_once()
        notes = self.memory.list_project_memories(str(self.project))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['content'], 'later value')

    async def test_delete_reinsertion_refuses_success_and_retains_the_later_row(self):
        note = self.memory.remember_project(str(self.project), 'keep later value')
        self.memory._connection.execute('CREATE TRIGGER restore_note AFTER DELETE ON project_memories BEGIN '
            'INSERT INTO project_memories VALUES(OLD.id,OLD.project_id,OLD.content,OLD.tags_json,OLD.created_at,OLD.updated_at); END')
        with patch.object(self.memory, 'forget_project', wraps=self.memory.forget_project) as write:
            with self.assertRaises(RuntimeError):await self.execute('forget', id=note['id'])
            write.assert_called_once()
        self.assertEqual(self.memory.project_memory(str(self.project), note['id']), note)

    async def test_every_saved_field_is_checked_and_wrong_project_cannot_delete(self):
        original = self.memory.remember_project
        for field, value in (('content', 'changed'), ('tags_json', '["other"]'), ('created_at', 'other')):
            def competing_write(*args, **kwargs):
                note = original(*args, **kwargs)
                self.memory._connection.execute('UPDATE project_memories SET ' + field + '=? WHERE id=?', (value, note['id']))
                self.memory._connection.commit()
                return note
            with patch.object(self.memory, 'remember_project', side_effect=competing_write):
                with self.assertRaises(RuntimeError):await self.execute('remember', content='expected')
        other = self.root / 'other'; other.mkdir()
        note = original(str(other), 'other project')
        missing = await self.execute('forget', id=note['id'])
        self.assertFalse(missing['removed'])
        self.assertEqual(self.memory.project_memory(str(other), note['id']), note)

    async def test_private_write_readback_and_storage_label_share_the_same_lock(self):
        self.memory.set_private(True)
        entered, release = threading.Event(), threading.Event()
        original = self.memory.project_memory
        def blocked(*args):
            entered.set()
            if not release.wait(2):raise AssertionError('Fixture did not release')
            return original(*args)
        switch = None
        with patch.object(self.memory, 'project_memory', side_effect=blocked):
            task = asyncio.create_task(self.execute('remember', content='private owned canary'))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                switch = asyncio.create_task(asyncio.to_thread(self.memory.set_private, False))
                await asyncio.sleep(.02)
                self.assertFalse(switch.done())
            finally:
                release.set()
                if switch:await switch
            saved = await task
        self.assertEqual(saved['storage'], 'RAM_ONLY')
        self.assertEqual(self.memory.list_project_memories(str(self.project)), [])
        self.assertNotIn(b'private owned canary', b''.join(p.read_bytes() for p in self.root.glob('memory.db*')))

    async def test_search_exact_limit_does_not_claim_an_unseen_extra_note(self):
        self.memory.remember_project(str(self.project), 'first')
        observed = await self.execute('search', limit=1)
        self.assertFalse(observed['has_more'])
        self.memory.remember_project(str(self.project), 'second')
        self.assertTrue((await self.execute('search', limit=1))['has_more'])

    async def test_incomplete_or_mistyped_evidence_never_becomes_a_verified_receipt(self):
        saved = await self.execute('remember', content='owned note')
        self.assertFalse(evaluate_result('memory.project.remember', {'verified': True}).verified)
        for field, value in (('verified', 1), ('verification_scope', 'row_exists'), ('storage', 'UNKNOWN'), ('memory', {})):
            bad = copy.deepcopy(saved); bad[field] = value
            self.assertFalse(evaluate_result('memory.project.remember', bad).verified)
            with patch.object(self.memory, 'remember_project_verified', return_value=bad):
                with self.assertRaises(ValidationError):await self.execute('remember', content='owned note')

    async def test_contracts_keep_original_permission_and_confirmation_boundaries(self):
        for operation, permission, confirmed in (('search', Permission.SAFE, False),
                                                ('remember', Permission.SENSITIVE, True),
                                                ('forget', Permission.DESTRUCTIVE, True)):
            spec = self.registry.get('memory.project.' + operation)
            self.assertEqual(spec.permission, permission)
            self.assertEqual(spec.requires_confirmation, confirmed)
            self.assertEqual(spec.public()['contract_gaps'], [])
            self.assertTrue(spec.offline_available)

    async def test_general_memory_commit_readback_keeps_project_notes_separate(self):
        register_builtin_tools(self.registry)
        project_note = self.memory.remember_project(str(self.project), 'project only')
        saved = await self.registry.execute(self.registry.get('memory.remember'), {'content': 'general only', 'tags': [' A ']})
        self.assertEqual(self.memory.explicit_memory(saved['memory']['id']), saved['memory'])
        self.assertTrue(evaluate_result('memory.remember', saved).verified)
        observed = await self.registry.execute(self.registry.get('memory.search'), {})
        self.assertEqual(observed['storage'], 'PERSISTENT')
        self.assertEqual([note['content'] for note in observed['memories']], ['general only'])
        deleted = await self.registry.execute(self.registry.get('memory.forget'), {'id': saved['memory']['id']})
        self.assertTrue(evaluate_result('memory.forget', deleted).verified)
        self.assertEqual(self.memory.project_memory(str(self.project), project_note['id']), project_note)

    async def test_general_memory_corruption_and_delete_reinsertion_do_not_report_success(self):
        register_builtin_tools(self.registry)
        self.memory._connection.execute("CREATE TRIGGER corrupt_general AFTER INSERT ON memories "
                                        "BEGIN UPDATE memories SET content='later' WHERE id=NEW.id; END")
        with patch.object(self.memory, 'remember', wraps=self.memory.remember) as write:
            with self.assertRaises(RuntimeError):
                await self.registry.execute(self.registry.get('memory.remember'), {'content': 'expected'})
            write.assert_called_once()
        note = self.memory.list_memories()[0]
        self.assertEqual(note['content'], 'later')
        self.memory._connection.execute('CREATE TRIGGER restore_general AFTER DELETE ON memories BEGIN '
            'INSERT INTO memories VALUES(OLD.id,OLD.content,OLD.tags_json,OLD.created_at,OLD.updated_at); END')
        with patch.object(self.memory, 'forget', wraps=self.memory.forget) as write:
            with self.assertRaises(RuntimeError):
                await self.registry.execute(self.registry.get('memory.forget'), {'id': note['id']})
            write.assert_called_once()
        self.assertEqual(self.memory.list_memories()[0], note)

    async def test_general_private_readback_is_ram_only_and_empty_notes_are_refused(self):
        register_builtin_tools(self.registry)
        with self.assertRaises(ValueError):
            await self.registry.execute(self.registry.get('memory.remember'), {'content': '   '})
        self.memory.set_private(True)
        saved = await self.registry.execute(self.registry.get('memory.remember'), {'content': 'general private canary'})
        self.assertEqual(saved['storage'], 'RAM_ONLY')
        self.assertEqual((await self.registry.execute(self.registry.get('memory.search'), {}))['storage'], 'RAM_ONLY')
        self.memory.set_private(False)
        self.assertEqual(self.memory.list_memories(), [])
        self.assertNotIn(b'general private canary', b''.join(p.read_bytes() for p in self.root.glob('memory.db*')))

    async def test_general_contracts_and_confirmation_gates_refuse_incomplete_receipts(self):
        register_builtin_tools(self.registry)
        for name, confirmed in (('memory.search', False), ('memory.remember', False), ('memory.forget', True)):
            spec = self.registry.get(name)
            self.assertEqual(spec.public()['contract_gaps'], [])
            self.assertEqual(spec.requires_confirmation, confirmed)
        for name in ('memory.remember', 'memory.forget'):
            self.assertFalse(evaluate_result(name, {'verified': True}).verified)
