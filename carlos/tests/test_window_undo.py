import copy
import logging
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ev.desktop import DesktopWorldModel, KWinBridge
from ev.desktop.world import WorldSnapshot
from ev.tools import ToolContext
from ev.tools.builtin import desktop_undo_window_change


class WindowUndoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous = {'id': 'owned-window', 'pid': 42, 'app_id': 'fixture',
                         'resource_class': 'fixture', 'normal': True, 'special': False,
                         'geometry': {'x': 20, 'y': 20, 'width': 800, 'height': 600},
                         'output': 'original', 'desktops': ['work', 'games'],
                         'on_all_desktops': False, 'maximize_mode': 0, 'maximized': False,
                         'minimized': False, 'fullscreen': False, 'tiled': False}
        self.output = {'name': 'original', 'manufacturer': 'fixture', 'model': 'monitor',
                       'serial_number': '12345', 'geometry': {'x': 0, 'y': 0, 'width': 1920, 'height': 1080}}
        self.world = {'windows': [copy.deepcopy(self.previous)], 'outputs': [self.output],
                      'desktops': [{'id': 'work'}, {'id': 'games'}]}
        self.model = DesktopWorldModel(KWinBridge(Path('/missing'), logging.getLogger('window-undo')))
        self.context = ToolContext({}, None, logging.getLogger('window-undo'), desktop=self.model)
        self.model.snapshot = AsyncMock(side_effect=lambda **_: copy.deepcopy(self.world))
        self.model.bridge.request = AsyncMock()

    def remember(self):
        self.model._snapshot = WorldSnapshot(1, copy.deepcopy(self.world))
        self.model.remember_window(self.previous, 'layout:left')

    async def restore(self, actual=None):
        self.remember()
        self.model.snapshot.side_effect = [copy.deepcopy(self.world),
            {**copy.deepcopy(self.world), 'windows': [copy.deepcopy(actual or self.previous)]}]
        with patch('ev.tools.builtin.asyncio.sleep', new=AsyncMock()):
            return await desktop_undo_window_change({}, self.context)

    async def test_minimized_fullscreen_and_partial_maximize_restore_all_states(self):
        for mode in (0, 1, 2, 3):
            with self.subTest(mode=mode):
                self.previous.update(maximize_mode=mode, maximized=mode == 3,
                                     minimized=True, fullscreen=True)
                self.model.bridge.request.reset_mock()
                result = await self.restore()
                self.assertTrue(result['verified'], result)
                calls = self.model.bridge.request.await_args_list
                actions = [call.args[0] for call in calls]
                self.assertLess(actions.index('fullscreen'), actions.index('minimize'))
                maximize = [call for call in calls if call.args[0] == 'maximize']
                self.assertEqual(len(maximize), bool(mode))
                if mode:
                    self.assertEqual(maximize[0].args[1]['mode'], mode)
                assignment = next(call.args[1] for call in calls if call.args[0] == 'move_to_desktop')
                self.assertEqual(assignment['desktop_ids'], ['work', 'games'])
                self.assertFalse(assignment['all_desktops'])
                self.assertTrue(result['output_identity_guarded'])
                self.assertTrue(result['output_identity_verified'])
                self.assertIsNone(self.model.peek_window_restore())

    async def test_all_desktops_and_legacy_maximized_state_remain_supported(self):
        self.previous.update(desktops=[], on_all_desktops=True, maximized=True)
        self.previous.pop('maximize_mode')
        result = await self.restore()
        self.assertTrue(result['verified'])
        calls = self.model.bridge.request.await_args_list
        assignment = next(call.args[1] for call in calls if call.args[0] == 'move_to_desktop')
        self.assertTrue(assignment['all_desktops'])
        maximize = next(call.args[1] for call in calls if call.args[0] == 'maximize')
        self.assertNotIn('mode', maximize)

    async def test_missing_monitor_workspace_changed_identity_or_tile_refuses_before_mutation(self):
        cases = ('missing_monitor', 'missing_workspace', 'replacement_monitor', 'ambiguous_monitor',
                 'changed_window', 'tiled', 'invalid_mode', 'invalid_geometry', 'moved_monitor')
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case == 'tiled':
                    self.previous['tiled'] = True
                elif case == 'invalid_mode':
                    self.previous['maximize_mode'] = True
                elif case == 'invalid_geometry':
                    self.previous['geometry']['width'] = 0
                self.remember()
                if case == 'missing_monitor':
                    self.world['outputs'] = []
                elif case == 'missing_workspace':
                    self.world['desktops'].pop()
                elif case == 'replacement_monitor':
                    self.output['serial_number'] = 'replacement'
                elif case == 'ambiguous_monitor':
                    self.world['outputs'].append(copy.deepcopy(self.output))
                elif case == 'changed_window':
                    self.world['windows'][0]['pid'] = 43
                elif case == 'moved_monitor':
                    self.output['geometry']['x'] = 2000
                result = await desktop_undo_window_change({}, self.context)
                self.assertFalse(result['verified'], case)
                self.model.bridge.request.assert_not_awaited()
                self.assertIsNotNone(self.model.peek_window_restore())

    async def test_same_hardware_on_new_connector_is_guarded_and_verified(self):
        self.remember()
        self.output['name'] = 'new-connector'
        actual = {**copy.deepcopy(self.previous), 'output': 'new-connector'}
        self.model.snapshot.side_effect = [copy.deepcopy(self.world),
            {**copy.deepcopy(self.world), 'windows': [actual]}]
        with patch('ev.tools.builtin.asyncio.sleep', new=AsyncMock()):
            result = await desktop_undo_window_change({}, self.context)
        self.assertTrue(result['verified'])
        args = self.model.bridge.request.await_args_list[0].args[1]
        self.assertEqual(args['output'], 'new-connector')
        self.assertEqual(args['expected_output_identity']['serial_number'], '12345')

    async def test_all_desktops_requires_empty_assignment_and_connector_fallback_is_explicit(self):
        self.output['serial_number'] = ''
        self.previous.update(desktops=[], on_all_desktops=True)
        result = await self.restore()
        self.assertTrue(result['verified'])
        self.assertFalse(result['output_identity_guarded'])
        self.assertIsNone(result['output_identity_verified'])
        actual = {**copy.deepcopy(self.previous), 'desktops': ['work']}
        self.assertFalse((await self.restore(actual))['verified'])

    async def test_final_wrong_mode_extra_workspace_or_monitor_replacement_does_not_claim_restore(self):
        for case in ('wrong_mode', 'extra_workspace', 'wrong_minimized_state', 'changed_identity', 'replaced_output'):
            with self.subTest(case=case):
                self.setUp()
                self.previous['maximize_mode'] = 1
                actual = copy.deepcopy(self.previous)
                if case == 'wrong_mode':
                    actual['maximize_mode'] = 0
                elif case == 'extra_workspace':
                    actual['desktops'].append('extra')
                elif case == 'wrong_minimized_state':
                    self.previous['minimized'] = True
                elif case == 'changed_identity':
                    actual['app_id'] = 'replacement'
                self.remember()
                final = {**copy.deepcopy(self.world), 'windows': [actual]}
                if case == 'replaced_output':
                    final['outputs'][0]['serial_number'] = 'replacement'
                self.model.snapshot.side_effect = [copy.deepcopy(self.world), final]
                with patch('ev.tools.builtin.asyncio.sleep', new=AsyncMock()):
                    result = await desktop_undo_window_change({}, self.context)
                self.assertFalse(result['verified'])
                self.assertIsNotNone(self.model.peek_window_restore())

    def test_saved_monitor_metadata_cannot_be_changed_through_external_dictionary(self):
        self.remember()
        self.output['serial_number'] = 'replacement'
        saved = self.model.peek_window_restore()
        self.assertEqual(saved['output']['serial_number'], '12345')
        saved['output']['serial_number'] = 'another'
        self.assertEqual(self.model.peek_window_restore()['output']['serial_number'], '12345')

    async def test_explicit_window_guard_refuses_another_windows_history_without_query_or_mutation(self):
        self.remember()
        result = await desktop_undo_window_change({'window_id': 'another-window'}, self.context)
        self.assertFalse(result['verified'])
        self.model.snapshot.assert_not_awaited()
        self.model.bridge.request.assert_not_awaited()
        self.assertIsNotNone(self.model.peek_window_restore())

    async def test_closed_window_discards_only_that_record_without_replaying_an_older_change(self):
        older = self.model.remember_window({**self.previous, 'id': 'older-window'}, 'move')
        self.remember()
        self.world.update(windows=[], captured_at_monotonic=time.monotonic())
        result = await desktop_undo_window_change({}, self.context)
        self.assertFalse(result['verified'])
        self.assertTrue(result['discarded'])
        self.assertEqual(self.model.peek_window_restore(), older)
        self.model.bridge.request.assert_not_awaited()

    async def test_animation_deleted_window_can_be_discarded_but_hidden_special_or_incomplete_evidence_cannot(self):
        for fields in ({'deleted': True}, {'normal': False}, {'special': True}):
            self.model._window_history.clear()
            self.remember()
            self.world.update(windows=[{**self.previous, **fields}], captured_at_monotonic=time.monotonic())
            result = await desktop_undo_window_change({}, self.context)
            self.assertEqual(result['discarded'], fields == {'deleted': True})
            self.model.bridge.request.assert_not_awaited()
        for data in ({'windows': []}, {'windows': [], 'windows_truncated': True, 'captured_at_monotonic': time.monotonic()},
                     {'windows': [], 'captured_at_monotonic': time.monotonic()-3}):
            self.model._window_history.clear()
            self.remember()
            self.world = {**self.world, **data}
            if 'captured_at_monotonic' not in data:
                self.world.pop('captured_at_monotonic', None)
            result = await desktop_undo_window_change({}, self.context)
            self.assertFalse(result['discarded'])
            self.assertIsNotNone(self.model.peek_window_restore())
            self.model.bridge.request.assert_not_awaited()

    async def test_success_consumes_its_own_record_and_preserves_a_newer_action(self):
        self.remember()
        newer = None
        async def concurrent_action(*_args):
            nonlocal newer
            if newer is None:
                newer = self.model.remember_window({**self.previous, 'id': 'newer-window'}, 'move')
            return {}
        self.model.bridge.request.side_effect = concurrent_action
        with patch('ev.tools.builtin.asyncio.sleep', new=AsyncMock()):
            result = await desktop_undo_window_change({}, self.context)
        self.assertTrue(result['verified'])
        self.assertTrue(result['history_consumed'])
        self.assertEqual(self.model.peek_window_restore(), newer)
        self.assertEqual(len(self.model._window_history), 1)

    async def test_discard_after_observation_preserves_a_newer_action_and_no_target_guard_is_bypassed(self):
        self.remember()
        newer = None
        async def concurrent_observation(**_args):
            nonlocal newer
            newer = self.model.remember_window({**self.previous, 'id': 'newer-window'}, 'move')
            return {**self.world, 'windows': [], 'captured_at_monotonic': time.monotonic()}
        self.model.snapshot.side_effect = concurrent_observation
        result = await desktop_undo_window_change({'window_id': self.previous['id']}, self.context)
        self.assertTrue(result['discarded'])
        self.assertEqual(self.model.peek_window_restore(), newer)
        self.assertEqual(len(self.model._window_history), 1)
        self.model.bridge.request.assert_not_awaited()

    def test_consuming_an_absent_or_changed_record_never_pops_the_current_head(self):
        self.remember()
        expected = self.model.peek_window_restore()
        expected['action'] = 'different'
        self.assertIsNone(self.model.consume_window_restore(expected))
        self.assertIsNotNone(self.model.peek_window_restore())

    def test_absent_window_failure_guidance_keeps_the_concrete_reason_and_disables_repair(self):
        from ev.planner import TaskPlanner, PlanStep
        from ev.tools.results import evaluate_result

        evidence = {'verified': False, 'discarded': True,
                    'message': "Removed that closed window's undo record; no other window moved."}
        result = evaluate_result('desktop.window.undo_last', evidence)
        self.assertEqual(result.error, evidence['message'])
        self.assertFalse(result.ok)
        self.assertFalse(result.retryable)
        planner = TaskPlanner.__new__(TaskPlanner)
        step = PlanStep('undo', 'desktop.window.undo_last', {}, permission_class='LOW_RISK')
        self.assertEqual(planner._failure_type(step, result.error), 'STALE_TARGET')
        self.assertFalse(planner.capability_gap(step.tool, result.error, 'STALE_TARGET')['engineering_task_available'])


class BridgeUndoTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++') and shutil.which('pkg-config'), 'Qt compiler tools unavailable')
    def test_actual_javascript_bridge_preserves_modes_and_workspace_assignments(self):
        import shlex
        flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'Qt6Qml', 'Qt6Core'],
                               capture_output=True, text=True, timeout=3)
        if flags.returncode:
            self.skipTest('Qt JavaScript engine development files unavailable')
        with tempfile.TemporaryDirectory(prefix='carlos-bridge-test-') as directory:
            root = Path(directory)
            cpp = root / 'check.cpp'
            cpp.write_text('''#include <QCoreApplication>
#include <QFile>
#include <QJSEngine>
#include <cstdio>
int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    QFile input(argv[1]);
    if (!input.open(QIODevice::ReadOnly)) return 2;
    QJSEngine engine;
    auto result = engine.evaluate(QString::fromUtf8(input.readAll()));
    if (result.isError()) { std::fprintf(stderr, "%s at line %d\\n", qPrintable(result.toString()), result.property("lineNumber").toInt()); return 1; }
    return 0;
}
''')
            compiled = subprocess.run(['c++', '-std=c++17', '-O0', '-fPIC', '-pie', str(cpp), '-o', str(root / 'check'),
                                       *shlex.split(flags.stdout)], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            source = Path(__file__).resolve().parents[1] / 'assets/kwin/bridge.js'
            scenario = Path(__file__).with_name('fixtures') / 'window_undo_bridge.js'
            script = root / 'check.js'
            script.write_text('function callDBus() {}\n' + source.read_text() + '\n' + scenario.read_text())
            result = subprocess.run([str(root / 'check'), str(script)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
