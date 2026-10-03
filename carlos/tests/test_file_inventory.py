import asyncio
import hashlib
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.events import EventBus
from ev.permissions import Permission
from ev.tools.base import ToolContext, ToolRegistry, ValidationError, validate_schema
from ev.tools.builtin import find_file, list_directory, register_builtin_tools
from ev.tools.results import evaluate_result


class FileInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / 'allowed'
        self.root.mkdir()
        self.outside = self.base / 'outside'
        self.outside.mkdir()
        self.context = ToolContext({'security': {'allowed_roots': [str(self.root)],
                                                'max_tool_output_bytes': 65536,
                                                'max_file_read_bytes': 65536}},
                                   EventBus(), logging.getLogger('file-inventory'))
        self.registry = ToolRegistry(self.context)
        register_builtin_tools(self.registry)

    def tearDown(self):
        self.temporary.cleanup()

    def find(self, **arguments):
        return find_file({'root': str(self.root), 'query': 'owned', **arguments}, self.context)

    def listing(self, **arguments):
        return list_directory({'path': str(self.root), **arguments}, self.context)

    def test_search_does_not_follow_file_or_directory_symlinks_outside_root(self):
        secret = self.outside / 'owned-secret.txt'
        secret.write_text('outside fixture data')
        (self.root / 'owned-link.txt').symlink_to(secret)
        (self.root / 'linked-directory').symlink_to(self.outside, target_is_directory=True)
        result = self.find()
        self.assertEqual(result['results'], [])
        self.assertEqual(result['skipped_symlinks'], 2)
        self.assertFalse(result['truncated'])

    def test_search_exact_limit_is_complete_and_extra_match_is_partial(self):
        (self.root / 'owned-one.txt').write_text('one')
        exact = self.find(limit=1)
        self.assertFalse(exact['truncated'])
        self.assertEqual(exact['stop_reason'], 'complete')
        (self.root / 'owned-two.txt').write_text('two')
        partial = self.find(limit=1)
        self.assertTrue(partial['truncated'])
        self.assertEqual(partial['stop_reason'], 'result_limit')
        self.assertEqual(len(partial['results']), 1)
        self.assertFalse(evaluate_result('files.find', partial).verified)

    def test_search_hidden_and_excluded_build_directories_stay_excluded(self):
        (self.root / '.owned-hidden').write_text('hidden')
        (self.root / 'build').mkdir()
        (self.root / 'build' / 'owned-generated').write_text('generated')
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'Owned-note.txt').write_text('visible')
        self.assertEqual([row['name'] for row in self.find()['results']], ['Owned-note.txt'])
        names = {row['name'] for row in self.find(include_hidden=True)['results']}
        self.assertEqual(names, {'.owned-hidden', 'Owned-note.txt'})

    def test_search_entry_directory_and_elapsed_budgets_return_partial(self):
        for index in range(4):
            (self.root / f'owned-{index}').write_text('owned')
        with patch('ev.tools.file_inventory.MAXIMUM_ENTRIES', 2):
            result = self.find()
        self.assertEqual(result['scanned_entries'], 2)
        self.assertEqual(result['stop_reason'], 'entry_limit')
        self.assertTrue(result['truncated'])
        for index in range(3):
            (self.root / f'directory-{index}').mkdir()
        with patch('ev.tools.file_inventory.MAXIMUM_DIRECTORIES', 2):
            result = self.find()
        self.assertLessEqual(result['visited_directories'], 2)
        self.assertEqual(result['stop_reason'], 'directory_limit')
        with patch('ev.tools.file_inventory.time.monotonic', side_effect=[0, 6]):
            result = self.find()
        self.assertEqual(result['stop_reason'], 'time_limit')
        self.assertTrue(result['truncated'])

    @unittest.skipIf(os.geteuid() == 0, 'Root bypasses this actual directory permission refusal')
    def test_actual_unreadable_directory_is_partial_not_an_empty_verified_inventory(self):
        (self.root / 'owned-visible').write_text('visible')
        blocked = self.root / 'blocked'
        blocked.mkdir()
        (blocked / 'owned-inaccessible').write_text('inaccessible')
        blocked.chmod(0)
        try:
            result = self.find()
        finally:
            blocked.chmod(0o700)
        self.assertEqual([row['name'] for row in result['results']], ['owned-visible'])
        self.assertEqual(result['scan_errors'], 1)
        self.assertEqual(result['stop_reason'], 'scan_errors')
        self.assertTrue(result['truncated'])
        self.assertFalse(evaluate_result('files.find', result).verified)

    def test_listing_shows_link_inode_without_claiming_target_type_or_size(self):
        target = self.outside / 'fixture.txt'
        target.write_text('outside data' * 100)
        link = self.root / 'owned-link'
        link.symlink_to(target)
        row = self.listing()['entries'][0]
        self.assertTrue(row['is_symlink'])
        self.assertFalse(row['is_file'])
        self.assertFalse(row['is_directory'])
        self.assertEqual(row['size_bytes'], link.lstat().st_size)
        self.assertNotEqual(row['size_bytes'], target.stat().st_size)

    def test_listing_keeps_directory_first_sort_and_truthful_exact_limit(self):
        (self.root / 'z-directory').mkdir()
        (self.root / 'b-file').write_text('b')
        (self.root / 'A-file').write_text('a')
        exact = self.listing(limit=3)
        self.assertEqual([row['name'] for row in exact['entries']],
                         ['z-directory', 'A-file', 'b-file'])
        self.assertFalse(exact['truncated'])
        partial = self.listing(limit=2)
        self.assertEqual([row['name'] for row in partial['entries']], ['z-directory', 'A-file'])
        self.assertEqual(partial['stop_reason'], 'result_limit')
        self.assertTrue(partial['truncated'])

    def test_listing_entry_and_elapsed_limits_preserve_partial_evidence(self):
        for index in range(4):
            (self.root / str(index)).write_text('owned')
        with patch('ev.tools.file_inventory.MAXIMUM_ENTRIES', 2):
            partial = self.listing()
        self.assertEqual(partial['scanned_entries'], 2)
        self.assertEqual(partial['stop_reason'], 'entry_limit')
        self.assertTrue(partial['truncated'])
        with patch('ev.tools.file_inventory.time.monotonic', side_effect=[0, 6]):
            partial = self.listing()
        self.assertEqual(partial['stop_reason'], 'time_limit')
        self.assertTrue(partial['truncated'])

    def test_registered_outputs_conform_and_existing_path_permissions_remain(self):
        file = self.root / 'owned.txt'
        file.write_text('owned fixture\n')
        cases = [('files.find', {'root': str(self.root), 'query': 'owned'}),
                 ('files.list', {'path': str(self.root)}),
                 ('files.info', {'path': str(file)}), ('files.read', {'path': str(file)}),
                 ('files.hash', {'path': str(file)})]
        for name, arguments in cases:
            with self.subTest(name=name):
                spec, args = self.registry.validate(name, arguments)
                result = asyncio.run(self.registry.execute(spec, args))
                validate_schema(result, spec.output_schema)
                self.assertFalse(spec.public()['contract_gaps'])
                self.assertTrue(spec.read_only)
                self.assertTrue(spec.offline_available)
                self.assertFalse(spec.reversible)
                self.assertTrue(evaluate_result(name, result).verified)
                if name == 'files.read':
                    self.assertEqual(spec.permission, Permission.SENSITIVE)
                    self.assertEqual(result['content'], 'owned fixture\n')
                if name == 'files.hash':
                    self.assertEqual(result['sha256'], hashlib.sha256(file.read_bytes()).hexdigest())
                with self.assertRaises(ValidationError):
                    validate_schema({**result, 'unexpected': 'unowned'}, spec.output_schema)
        (self.outside / 'private.txt').write_text('outside fixture')
        with self.assertRaises(ValidationError):
            spec, args = self.registry.validate('files.read', {'path': str(self.outside / 'private.txt')})
            asyncio.run(self.registry.execute(spec, args))

    def test_search_rejects_a_file_root(self):
        file = self.root / 'owned.txt'
        file.write_text('fixture')
        with self.assertRaisesRegex(ValidationError, 'not a directory'):
            self.find(root=str(file))


if __name__ == '__main__':
    unittest.main()
