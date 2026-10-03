import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ev.events import EventBus
from ev.permissions import Permission
from ev.tools import ToolContext, ToolRegistry, ValidationError, register_builtin_tools
from ev.tools.base import validate_schema
from ev.tools.builtin import copy_path, move_path
from ev.tools.results import evaluate_result


class FileTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.destination = self.root / 'destination'
        self.context = ToolContext({'security': {'allowed_roots': [str(self.root)],
                                                'max_tool_output_bytes': 65536}},
                                   EventBus(), logging.getLogger('file-transfer'))

    def tearDown(self):
        self.temporary.cleanup()

    def transfer(self, operation):
        return operation({'source': str(self.source), 'destination': str(self.destination)},
                         self.context)

    def test_same_size_corruption_after_real_move_is_not_verified_or_replayed(self):
        self.source.write_bytes(b'first')
        original = shutil.move
        def corrupt(*args, **kwargs):
            moved = original(*args, **kwargs)
            self.destination.write_bytes(b'later')
            return moved
        with patch('ev.tools.builtin.shutil.move', side_effect=corrupt) as dispatch:
            result = self.transfer(move_path)
        self.assertFalse(result['verified'])
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(self.destination.read_bytes(), b'later')
        self.assertFalse(self.source.exists())
        receipt = evaluate_result('files.move', result)
        self.assertFalse(receipt.verified)
        self.assertFalse(receipt.retryable)
        self.assertIsNone(receipt.changed_state)

    def test_missing_child_after_real_directory_move_is_not_verified(self):
        self.source.mkdir()
        (self.source / 'owned.txt').write_text('owned data')
        original = shutil.move
        def remove(*args, **kwargs):
            moved = original(*args, **kwargs)
            (self.destination / 'owned.txt').unlink()
            return moved
        with patch('ev.tools.builtin.shutil.move', side_effect=remove):
            result = self.transfer(move_path)
        self.assertFalse(result['verified'])
        self.assertEqual(list(self.destination.iterdir()), [])

    @unittest.skipIf(os.geteuid() == 0, 'Root bypasses actual directory permission refusal')
    def test_unreadable_child_refuses_before_any_destination_is_created(self):
        self.source.mkdir()
        blocked = self.source / 'blocked'
        blocked.mkdir()
        (blocked / 'owned.txt').write_text('owned data')
        blocked.chmod(0)
        try:
            for operation in (copy_path, move_path):
                with self.subTest(operation=operation.__name__):
                    with self.assertRaises((ValidationError, OSError)):
                        self.transfer(operation)
                    self.assertFalse(self.destination.exists())
                    self.assertTrue(self.source.exists())
        finally:
            blocked.chmod(0o700)

    def test_links_special_files_and_budget_refuse_before_dispatch(self):
        self.source.mkdir()
        (self.source / 'link').symlink_to(self.root / 'outside-owned-file')
        for operation in (copy_path, move_path):
            with self.assertRaises(ValidationError):
                self.transfer(operation)
            self.assertFalse(self.destination.exists())
        (self.source / 'link').unlink()
        os.mkfifo(self.source / 'pipe')
        for operation in (copy_path, move_path):
            with self.assertRaises(ValidationError):
                self.transfer(operation)
            self.assertFalse(self.destination.exists())
        (self.source / 'pipe').unlink()
        for index in range(3):
            (self.source / str(index)).write_text('owned')
        with patch('ev.tools.file_transfer.MAXIMUM_ENTRIES', 2):
            for operation in (copy_path, move_path):
                with self.assertRaisesRegex(ValidationError, 'verification limit'):
                    self.transfer(operation)
                self.assertFalse(self.destination.exists())

    def test_allowed_root_and_nested_move_refuse_without_changes(self):
        with self.assertRaisesRegex(ValidationError, 'allowed-root'):
            move_path({'source': str(self.root), 'destination': str(self.destination)}, self.context)
        self.source.mkdir()
        with self.assertRaisesRegex(ValidationError, 'inside the source'):
            move_path({'source': str(self.source), 'destination': str(self.source / 'nested')}, self.context)
        self.assertEqual(list(self.source.iterdir()), [])

    def test_readback_link_replacement_does_not_verify_copy(self):
        self.source.write_bytes(b'original')
        target = self.root / 'owned-target'
        target.write_bytes(b'original')
        original = shutil.copy2
        def replace(*args, **kwargs):
            copied = original(*args, **kwargs)
            self.destination.unlink()
            self.destination.symlink_to(target)
            return copied
        with patch('ev.tools.builtin.shutil.copy2', side_effect=replace):
            result = self.transfer(copy_path)
        self.assertFalse(result['verified'])
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(target.read_bytes(), b'original')

    def test_registered_real_file_and_directory_results_have_scoped_contracts(self):
        registry = ToolRegistry(self.context)
        register_builtin_tools(registry)
        for kind in ('file', 'directory'):
            source = self.root / ('source-' + kind)
            if kind == 'file':
                source.write_bytes(b'owned data')
            else:
                source.mkdir()
                (source / 'empty').mkdir()
                (source / '.hidden').write_bytes(b'owned data')
            for name in ('files.copy', 'files.move'):
                destination = self.root / (name + '-' + kind)
                spec, arguments = registry.validate(name, {'source': str(source),
                                                           'destination': str(destination)})
                result = asyncio.run(registry.execute(spec, arguments))
                self.assertTrue(result['verified'])
                self.assertEqual(result['kind'], kind)
                if kind == 'file':
                    expected = hashlib.sha256(b'owned data').hexdigest()
                    self.assertEqual(result['sha256'], expected)
                    self.assertEqual(destination.read_bytes(), b'owned data')
                else:
                    self.assertTrue((destination / 'empty').is_dir())
                    self.assertEqual((destination / '.hidden').read_bytes(), b'owned data')
                self.assertEqual(spec.permission, Permission.SENSITIVE)
                self.assertFalse(spec.read_only)
                self.assertFalse(spec.reversible)
                self.assertTrue(spec.offline_available)
                self.assertFalse(spec.public()['contract_gaps'])
                receipt = evaluate_result(name, result)
                self.assertEqual(receipt.scope, 'filesystem_content_readback')
                self.assertTrue(receipt.changed_state)
                with self.assertRaises(ValidationError):
                    validate_schema({**result, 'unexpected': 'data'}, spec.output_schema)


if __name__ == '__main__':
    unittest.main()
