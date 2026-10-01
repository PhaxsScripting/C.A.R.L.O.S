import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from ev.paths import Paths
from ev.service import CarlosCore
from ev.task_journal import TaskJournal
from ev.tools.project_memory import select_project


class JournalScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.db'
        self.journal = TaskJournal(self.path)

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def unfinished(self, task, project):
        self.journal.begin(task, task + '_canary', project=project)
        self.journal.finish(task, {'status': 'failed'})

    def test_legacy_upgrade_keeps_general_tasks_and_step_receipts(self):
        path = self.path.with_name('legacy.db')
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE agent_tasks (id TEXT PRIMARY KEY,request TEXT NOT NULL,parent_id TEXT,
                    status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,detail TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE agent_steps (id INTEGER PRIMARY KEY,task_id TEXT NOT NULL,tool TEXT NOT NULL,
                    arguments TEXT NOT NULL,status TEXT NOT NULL,started REAL NOT NULL,finished REAL,receipt TEXT NOT NULL DEFAULT '{}');
                INSERT INTO agent_tasks VALUES('old','legacy request',NULL,'FAILED',1,2,'{}');
                INSERT INTO agent_steps VALUES(1,'old','system.identity','{}','COMPLETED',1,2,'{"verified":true}');
            ''')
        journal = TaskJournal(path)
        self.assertEqual(journal.latest_resumable(project='')['id'], 'old')
        self.assertIsNone(journal.get('old', project='/alpha'))
        self.assertTrue(journal.get_page('old', project='')['steps'][0]['receipt']['verified'])
        journal.close()
        journal = TaskJournal(path)
        self.assertEqual(journal.get('old', project='')['request'], 'legacy request')
        journal.close()

    def test_reads_pages_and_resume_never_cross_project(self):
        self.unfinished('alpha', '/alpha')
        self.unfinished('beta', '/beta')
        self.unfinished('general', '')
        self.assertEqual(self.journal.latest_resumable(project='/alpha')['id'], 'alpha')
        self.assertEqual([r['id'] for r in self.journal.recent(project='/beta')], ['beta'])
        self.assertIsNone(self.journal.get('alpha', project='/beta'))
        self.assertIsNone(self.journal.get_page('alpha', project='/beta'))
        self.assertEqual(self.journal.latest_resumable(project='')['id'], 'general')

    def test_success_boundary_applies_inside_each_project(self):
        self.unfinished('alpha-old', '/alpha')
        self.journal.begin('alpha-new', 'ordinary answer', project='/alpha')
        self.journal.finish('alpha-new', {'status': 'completed'})
        self.unfinished('beta', '/beta')
        self.assertIsNone(self.journal.latest_resumable(project='/alpha'))
        self.assertEqual(self.journal.latest_resumable(project='/beta')['id'], 'beta')

    def test_parent_scope_cannot_be_forged_or_lost(self):
        self.unfinished('alpha', '/alpha')
        for parent in ('alpha', 'missing'):
            with self.assertRaises(ValueError):
                self.journal.begin('bad', 'revision', parent, project='/beta')
        self.assertIsNone(self.journal.get('bad'))
        self.journal.begin('good', 'revision', 'alpha', project='/alpha')
        self.assertEqual(self.journal.get('good')['parent_id'], 'alpha')

    def test_private_scoped_receipts_never_touch_saved_database(self):
        self.unfinished('saved', '/alpha')
        before = self.path.read_bytes()
        self.journal.set_private(True)
        self.unfinished('private', '/beta')
        self.assertEqual(self.journal.recent(project='/alpha'), [])
        self.assertEqual(self.journal.latest_resumable(project='/beta')['id'], 'private')
        self.assertEqual(self.path.read_bytes(), before)
        self.journal.set_private(False)
        self.assertIsNone(self.journal.get('private'))
        self.assertEqual(self.journal.get('saved')['project'], '/alpha')

    def test_restart_preserves_scope_without_replaying_running_steps(self):
        self.journal.begin('running', 'fixture', project='/alpha')
        self.journal.start_step('running', 'files.text.create', {'path':'/alpha/file'})
        self.journal.close()
        recovered = TaskJournal(self.path)
        self.assertEqual(recovered.recover_interrupted(), 1)
        task = recovered.latest_resumable(project='/alpha')
        self.assertEqual(task['project'], '/alpha')
        self.assertEqual(task['steps'][0]['status'], 'INTERRUPTED_UNCERTAIN')
        self.assertIsNone(recovered.latest_resumable(project='/beta'))
        recovered.close()


class TaskScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = CarlosCore(paths=Paths(*(self.root / name for name in ('config','data','state','cache','run'))))
        self.core.config['security']['allowed_roots'] = [str(self.root)]
        self.core.config['assistant']['speak_responses'] = False
        self.alpha = self.root / 'alpha'; self.alpha.mkdir()
        self.beta = self.root / 'beta'; self.beta.mkdir()
        self.core.tools.execute = AsyncMock(return_value={'active_window_id':'fresh','windows':[]})
        self.core.brain.submit = AsyncMock(return_value={'status':'completed','execution_status':'EXECUTED_UNVERIFIED'})
        self.core._prepare_interactive_request = AsyncMock()
        self.journal = self.core.task_journal

    def tearDown(self):
        self.core.daily.close(); self.core.task_journal.close(); self.core.memory.close()
        self.temp.cleanup()

    async def select(self, path):
        await select_project({'project':str(path)}, self.core.tools.context)

    def unfinished(self, task, project):
        self.journal.begin(task, task + '_canary', project=str(project))
        self.journal.finish(task, {'status':'failed'})

    async def test_continue_uses_selected_scope_not_newest_workstation_task(self):
        self.unfinished('alpha-task', self.alpha)
        self.unfinished('beta-task', self.beta)
        await self.select(self.alpha)
        result = await self.core._submit_action_clauses('continue', 'resumed')
        self.assertEqual(result['status'], 'completed')
        saved = self.journal.get('resumed')
        self.assertEqual(saved['parent_id'], 'alpha-task')
        self.assertEqual(saved['project'], str(self.alpha))
        context = self.core.brain.submit.await_args.kwargs['resume_context']
        self.assertIn('alpha-task_canary', context)
        self.assertNotIn('beta-task_canary', context)

    async def test_empty_project_cannot_resume_other_history_or_observe(self):
        self.unfinished('alpha-task', self.alpha)
        await self.select(self.beta)
        result = await self.core._submit_action_clauses('continue', 'empty')
        self.assertEqual(result['status'], 'failed')
        self.core.tools.execute.assert_not_awaited()
        self.core.brain.submit.assert_not_awaited()
        self.assertIsNone(self.journal.get('empty'))

    async def test_request_keeps_original_scope_when_selection_changes_mid_execution(self):
        await self.select(self.alpha)
        async def run(*args):
            await self.select(self.beta)
            return {'status':'completed'}
        self.core._submit_action_clauses_impl = run
        result = await self.core._submit_action_clauses('inspect project', 'captured')
        self.assertEqual(result['conversation_project'], str(self.alpha))
        self.assertEqual(self.journal.get('captured')['project'], str(self.alpha))
        listed = await self.core.handle_request({'type':'agent.tasks.list','payload':{},'id':'list'})
        self.assertEqual(listed['project'], str(self.beta))
        self.assertEqual(listed['tasks'], [])

    async def test_wrong_scope_revision_does_not_interrupt_or_read_private_request(self):
        self.unfinished('alpha-task', self.alpha)
        await self.select(self.beta)
        running = asyncio.create_task(asyncio.sleep(10))
        self.core._interactive_task = running
        self.core._interactive_correlation = 'alpha-task'
        try:
            result = await self.core._steer_task('alpha-task', 'change it', 'wrong')
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('canary', json.dumps(result))
            self.assertFalse(running.done())
            self.core._prepare_interactive_request.assert_not_awaited()
            self.core.tools.execute.assert_not_awaited()
        finally:
            running.cancel(); await asyncio.gather(running, return_exceptions=True)
            self.core._interactive_task = None

    async def test_ipc_and_model_receipt_tools_filter_exact_project(self):
        self.unfinished('alpha-task', self.alpha)
        self.unfinished('beta-task', self.beta)
        await self.select(self.beta)
        listed = await self.core.handle_request({'type':'agent.tasks.list','payload':{},'id':'list'})
        self.assertEqual([r['id'] for r in listed['tasks']], ['beta-task'])
        with self.assertRaisesRegex(ValueError, 'task not found'):
            await self.core.handle_request({'type':'agent.tasks.get','payload':{'id':'alpha-task'},'id':'get'})
        detail = self.core.tools.get('agent.task_status')
        denied = await detail.executor({'id':'alpha-task'}, self.core.tools.context)
        self.assertFalse(denied['ok'])
        self.assertNotIn('alpha-task_canary', json.dumps(denied))
        recent = await self.core.tools.get('agent.history').executor({}, self.core.tools.context)
        self.assertEqual(recent['project'], str(self.beta))
        self.assertEqual([r['id'] for r in recent['tasks']], ['beta-task'])
        allowed = await detail.executor({'id':'beta-task'}, self.core.tools.context)
        self.assertEqual(allowed['task']['project'], str(self.beta))

    async def test_scope_change_during_steering_lookup_blocks_before_cancel(self):
        self.unfinished('alpha-task', self.alpha)
        self.core._project_scope = AsyncMock(side_effect=[str(self.alpha), str(self.beta)])
        result = await self.core._steer_task('alpha-task', 'change it', 'changed')
        self.assertEqual(result['status'], 'blocked')
        self.core._prepare_interactive_request.assert_not_awaited()
        self.assertIsNone(self.journal.get('changed'))

    async def test_explicit_previous_cannot_be_reinterpreted_in_other_scope(self):
        self.unfinished('alpha-task', self.alpha)
        await self.select(self.beta)
        result = await self.core._submit_action_clauses('change it', 'wrong', previous=self.journal.get('alpha-task'))
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(self.journal.get('wrong'))
        self.core.tools.execute.assert_not_awaited()

    async def test_same_project_pending_approval_stays_blocked(self):
        self.journal.begin('pending', 'fixture', project=str(self.alpha))
        self.journal.finish('pending', {'status':'confirmation_required'})
        await self.select(self.alpha)
        result = await self.core._steer_task('pending', 'change it', 'revision')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.journal.get('pending')['status'], 'WAITING_CONFIRMATION')
        self.assertIsNone(self.journal.get('revision'))
        self.core.tools.execute.assert_not_awaited()

    async def test_guest_cannot_read_persistent_project_receipts(self):
        self.unfinished('alpha-task', self.alpha)
        self.core._stop_all_actions = AsyncMock(return_value={})
        self.core.voice.set_privacy_mode = AsyncMock(return_value={})
        await self.core.privacy.set_mode('GUEST')
        self.assertEqual(await self.core._project_scope(), '')
        for kind in ('agent.tasks.list','agent.tasks.get'):
            result = await self.core.handle_request({'type':kind,'payload':{'id':'alpha-task'},'id':'guest'})
            self.assertNotIn('alpha-task_canary', json.dumps(result))
        await self.core.privacy.set_mode('NORMAL')
        self.assertEqual(self.journal.get('alpha-task')['project'], str(self.alpha))
