import asyncio
import copy
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.daily import DailyStore
from ev.events import EventBus
from ev.planner import PlanStep, TaskPlanner
from ev.tools.base import ToolContext, ToolRegistry, ValidationError, validate_schema
from ev.tools.personal import register_personal_tools
from ev.tools.personal_contracts import PERSONAL_MUTATION_SCHEMAS
from ev.tools.results import evaluate_result


class PersonalReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'daily.db'
        self.store = DailyStore(self.path)
        self.personal = self.store.personal
        context = ToolContext({'security': {'max_tool_output_bytes': 65536}},
                              EventBus(), logging.getLogger('personal-readback'), daily=self.store)
        self.registry = ToolRegistry(context)
        register_personal_tools(self.registry)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def call(self, name, arguments):
        spec, arguments = self.registry.validate(name, arguments)
        return asyncio.run(self.registry.execute(spec, arguments))

    def test_all_mutations_return_the_actual_committed_row(self):
        exercised = set()
        for kind, prefix in [('note', 'notes'), ('task', 'tasks'),
                             ('bookmark', 'bookmarks'), ('snippet', 'snippets')]:
            content = 'https://example.org/fixture' if kind == 'bookmark' else 'owned content'
            result = self.call(prefix + '.create', {'title': 'Owned ' + kind, 'content': content})
            exercised.add(prefix + '.create')
            operations = ['archive', 'restore']
            if kind == 'task':
                operations += ['complete', 'reopen']
            if kind == 'note':
                operations += ['append']
            for operation in ['create'] + operations:
                name = prefix + '.' + operation
                if operation != 'create':
                    arguments = {'identifier': result['item']['id']}
                    if operation == 'append':
                        arguments['content'] = 'second line'
                    result = self.call(name, arguments)
                    exercised.add(name)
                with sqlite3.connect(self.path) as db:
                    db.row_factory = sqlite3.Row
                    row = db.execute('SELECT * FROM personal_items WHERE id=?',
                                     (result['item']['id'],)).fetchone()
                self.assertEqual(dict(row), result['item'])
                spec = self.registry.get(name)
                self.assertFalse(spec.read_only)
                self.assertFalse(spec.reversible)
                self.assertTrue(spec.offline_available)
                self.assertFalse(spec.public()['contract_gaps'])
                evidence = evaluate_result(name, result)
                self.assertTrue(evidence.verified)
                self.assertTrue(evidence.changed_state)
                self.assertEqual(evidence.scope, 'committed_sqlite_readback')
                self.assertFalse(TaskPlanner._retryable(PlanStep('owned', name, {},
                                                              permission_class='LOW_RISK')))
        self.assertEqual(exercised, set(PERSONAL_MUTATION_SCHEMAS))
        self.assertEqual(len(exercised), 15)

    def test_later_writer_after_create_is_not_overwritten_or_reported_saved(self):
        original = self.personal._read_back
        calls = []
        def edit_then_read(expected):
            calls.append(expected['id'])
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE personal_items SET content=? WHERE id=?',
                           ('later writer secret', expected['id']))
            return original(expected)
        with patch.object(self.personal, '_read_back', side_effect=edit_then_read):
            with self.assertRaisesRegex(RuntimeError, 'committed readback') as failure:
                self.call('notes.create', {'title': 'Owned note', 'content': 'first secret'})
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', str(failure.exception))
        self.assertEqual(self.personal.read('note', 'Owned note')['item']['content'],
                         'later writer secret')

    def test_later_writer_after_append_is_preserved_without_replay(self):
        saved = self.personal.create('note', 'Owned note', 'first')['item']
        original = self.personal._read_back
        calls = []
        def edit_then_read(expected):
            calls.append(expected['content'])
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE personal_items SET content=? WHERE id=?',
                           ('later edit', expected['id']))
            return original(expected)
        with patch.object(self.personal, '_read_back', side_effect=edit_then_read):
            with self.assertRaisesRegex(RuntimeError, 'Inspect it'):
                self.call('notes.append', {'identifier': saved['id'], 'content': 'second'})
        self.assertEqual(calls, ['first\nsecond'])
        self.assertEqual(self.personal.read('note', saved['id'])['item']['content'], 'later edit')

    def test_deleted_committed_item_is_not_recreated(self):
        saved = self.personal.create('task', 'Owned task')['item']
        original = self.personal._read_back
        def delete_then_read(expected):
            with sqlite3.connect(self.path) as db:
                db.execute('DELETE FROM personal_items WHERE id=?', (expected['id'],))
            return original(expected)
        with patch.object(self.personal, '_read_back', side_effect=delete_then_read):
            with self.assertRaisesRegex(RuntimeError, 'committed readback'):
                self.call('tasks.complete', {'identifier': saved['id']})
        self.assertEqual(self.personal.listing('task', state='all')['items'], [])

    def test_readback_uses_id_even_when_another_title_matches_that_id(self):
        first = self.personal.create('note', 'Owned note', 'first')['item']
        self.personal.create('note', first['id'], 'other')
        result = self.personal.change('note', 'Owned note', 'append', 'second')
        self.assertEqual(result['item']['id'], first['id'])
        self.assertEqual(result['item']['content'], 'first\nsecond')

    def test_private_and_guest_readback_never_reads_or_persists_disk_contents(self):
        self.personal.create('note', 'Disk only', 'disk content')
        for guest in [False, True]:
            self.store.set_private(True, guest=guest)
            result = self.call('notes.create', {'title': 'RAM only', 'content': 'ram content'})
            result = self.call('notes.append', {'identifier': result['item']['id'], 'content': 'second'})
            self.assertEqual(result['item']['content'], 'ram content\nsecond')
            with sqlite3.connect(self.path) as db:
                titles = [row[0] for row in db.execute('SELECT title FROM personal_items')]
            self.assertEqual(titles, ['Disk only'])
            self.store.set_private(False)
            self.assertEqual(self.personal.listing('note')['items'][0]['title'], 'Disk only')

    def test_invalid_success_claims_fail_contract_and_evidence(self):
        result = self.call('notes.create', {'title': 'Owned note', 'content': 'owned'})
        schema = self.registry.get('notes.create').output_schema
        invalid = [dict(result, verified=False), dict(result, verification_scope='dispatch'),
                   dict(result, extra='unowned')]
        for key, value in [('kind', 'task'), ('done', True), ('updated', float('nan'))]:
            changed = copy.deepcopy(result)
            changed['item'][key] = value
            invalid.append(changed)
        for changed in invalid:
            with self.assertRaises(ValidationError):
                validate_schema(changed, schema)
        evidence = evaluate_result('notes.create', {'verified': True, 'item': result['item']})
        self.assertFalse(evidence.verified)
        self.assertIsNone(evidence.changed_state)
        self.assertFalse(evidence.retryable)


if __name__ == '__main__':
    unittest.main()
