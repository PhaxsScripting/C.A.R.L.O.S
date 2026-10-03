import copy
import logging
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ev.desktop import DesktopWorldModel, KWinBridge
from ev.desktop.world import WorldSnapshot
from ev.events import PhaxEventBus
from ev.planner import TaskPlanner
from ev.state import StateMachine
from ev.tools import ToolContext, ToolRegistry, ValidationError
from ev.tools.base import validate_schema
from ev.tools.builtin import register_builtin_tools
from ev.tools.window_pair import beside


class WindowPairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.world = {'current_desktop': 'work', 'captured_at_monotonic': time.monotonic(),
                      'outputs': [{'name': 'screen', 'enabled': True, 'manufacturer': 'Fixture',
                                   'model': 'Panel', 'serial_number': 'one', 'scale': 1,
                                   'geometry': {'x': 100, 'y': 200, 'width': 801, 'height': 600}}]}
        self.world['windows'] = [
            {'id': role, 'pid': pid, 'app_id': role, 'resource_class': role,
             'normal': True, 'special': False, 'moveable': True, 'resizeable': True,
             'output': 'screen', 'desktops': ['work'], 'on_all_desktops': False,
             'minimized': True, 'fullscreen': True, 'maximized': True, 'maximize_mode': 3,
             'tiled': False, 'geometry': {'x': 120, 'y': 220, 'width': 700, 'height': 500}}
            for role, pid in (('anchor', 123), ('target', 456))]
        self.model = DesktopWorldModel(KWinBridge(Path('/missing'), logging.getLogger('pair')))
        bus = PhaxEventBus()
        self.context = ToolContext({}, bus, logging.getLogger('pair'), desktop=self.model)
        self.model.snapshot = AsyncMock(side_effect=self.snapshot)
        self.expected = {'anchor': {'x': 100, 'y': 240, 'width': 400, 'height': 560},
                         'window': {'x': 500, 'y': 240, 'width': 401, 'height': 560}}
        self.model.bridge.request = AsyncMock(side_effect=self.dispatch)
        registry = ToolRegistry(self.context)
        register_builtin_tools(registry)
        self.registry = registry
        self.planner = TaskPlanner(registry, AsyncMock(), bus, StateMachine(bus))

    async def snapshot(self, **_):
        self.world['captured_at_monotonic'] = time.monotonic()
        self.model._snapshot = WorldSnapshot(time.monotonic(), copy.deepcopy(self.world))
        return copy.deepcopy(self.world)

    async def dispatch(self, action, arguments):
        self.assertEqual(action, 'beside')
        self.assertEqual(arguments['expected_windows']['anchor']['pid'], 123)
        self.assertEqual(arguments['expected_output']['serial_number'], 'one')
        for window, role in zip(self.world['windows'], ('anchor', 'window')):
            window.update(geometry=copy.deepcopy(self.expected[role]), minimized=False,
                          fullscreen=False, maximized=False, maximize_mode=0)
        return {'target_geometries': copy.deepcopy(self.expected)}

    async def run_pair(self):
        with patch('ev.tools.window_pair.asyncio.sleep', new=AsyncMock()):
            return await beside({'window_id': 'target', 'anchor_id': 'anchor'}, self.context)

    async def test_both_readbacks_and_separate_exact_undo_records(self):
        result = await self.run_pair()
        self.assertTrue(result['verified'])
        self.assertEqual(result['undo_steps'], 2)
        self.assertEqual(result['window']['id'], 'target')
        validate_schema(result, self.registry.get('desktop.window.beside').output_schema)
        target = self.model.consume_window_restore()
        anchor = self.model.consume_window_restore()
        self.assertEqual((target['window']['id'], anchor['window']['id']), ('target', 'anchor'))
        self.assertEqual(target['window']['maximize_mode'], 3)
        self.assertEqual(anchor['window']['geometry']['x'], 120)

    async def test_same_missing_closed_tiled_other_workspace_or_monitor_never_dispatches(self):
        for case in ('same', 'missing', 'deleted', 'tiled', 'workspace', 'output', 'resizeable', 'unresponsive'):
            with self.subTest(case=case):
                self.setUp()
                arguments = {'window_id': 'target', 'anchor_id': 'anchor'}
                window = self.world['windows'][1]
                if case == 'same': arguments['anchor_id'] = 'target'
                elif case == 'missing': self.world['windows'].pop()
                elif case in ('deleted', 'tiled', 'unresponsive'): window[case] = True
                elif case == 'workspace': window['desktops'] = ['other']
                elif case == 'output': window['output'] = 'another-screen'
                else: window['resizeable'] = False
                with self.assertRaises(ValidationError):
                    await beside(arguments, self.context)
                self.model.bridge.request.assert_not_awaited()
                self.assertIsNone(self.model.peek_window_restore())

    async def test_incomplete_or_stale_inventory_refuses(self):
        for case in ('truncated', 'stale', 'future', 'nan'):
            with self.subTest(case=case):
                self.setUp()
                if case == 'truncated': self.world['windows_truncated'] = True
                else:
                    self.world['captured_at_monotonic'] = {'stale': time.monotonic()-3,
                        'future': time.monotonic()+100, 'nan': float('nan')}[case]
                    self.model.snapshot.side_effect = None
                    self.model.snapshot.return_value = copy.deepcopy(self.world)
                with self.assertRaises(ValidationError): await self.run_pair()
                self.model.bridge.request.assert_not_awaited()

    async def test_wrong_geometry_identity_output_or_state_does_not_claim_success(self):
        for case in ('geometry', 'identity', 'state', 'output', 'missing'):
            with self.subTest(case=case):
                self.setUp()
                async def wrong(action, arguments):
                    result = await self.dispatch(action, arguments)
                    if case == 'geometry': self.world['windows'][0]['geometry']['width'] += 10
                    elif case == 'identity': self.world['windows'][0]['pid'] += 1
                    elif case == 'state': self.world['windows'][0]['fullscreen'] = True
                    elif case == 'output': self.world['outputs'][0]['serial_number'] = 'replacement'
                    else: self.world['windows'].pop()
                    return result
                self.model.bridge.request.side_effect = wrong
                self.assertFalse((await self.run_pair())['verified'])
                self.assertEqual(self.model.bridge.request.await_count, 1)
                self.assertIsNotNone(self.model.peek_window_restore())

    async def test_native_failure_retains_bounded_history_without_replay(self):
        self.model.bridge.request.side_effect = RuntimeError('Native window changed')
        with self.assertRaises(RuntimeError): await self.run_pair()
        self.assertEqual(self.model.bridge.request.await_count, 1)
        self.assertEqual(self.model.peek_window_restore()['window']['id'], 'target')

    def test_followup_is_bound_before_resolving_the_requested_window(self):
        self.planner.last_entities = {'window': {'id': 'remembered'}, 'window_at': time.monotonic()}
        plan = self.planner.try_plan('put Firefox beside that one', 'pair')
        self.assertEqual(plan.steps[0].arguments['description'], 'window-id:remembered')
        self.assertEqual(plan.steps[1].arguments['description'], 'Firefox')
        self.assertEqual(plan.steps[2].tool, 'desktop.window.beside')
        self.assertEqual(len(plan.goal_conditions), 2)
        self.assertFalse(self.planner._retryable(plan.steps[2], 'temporary failure'))
        self.assertFalse(self.registry.get('desktop.window.beside').public()['contract_gaps'])

    async def test_dry_run_never_calls_any_window_tool(self):
        plan = self.planner.try_plan('dry run, place Firefox next to Konsole', 'dry-pair')
        self.assertTrue(plan.dry_run)
        await self.planner.execute(plan)
        self.planner.request_tool.assert_not_awaited()

    def test_unbound_pronoun_and_incidental_text_do_not_guess(self):
        plan = self.planner.try_plan('move Firefox beside it', 'missing')
        self.assertEqual(plan.steps[0].arguments['description'], 'window-id:missing-context')
        self.assertIsNone(self.planner.try_plan('Read the label put Firefox beside Konsole', 'label'))

    async def test_completed_pair_replaces_old_correction_operation(self):
        self.planner.last_window_operation = {'tool': 'desktop.window.layout',
                                              'arguments': {'window_id': 'old', 'layout': 'center'}}
        async def request(payload, _correlation):
            name, arguments = payload['name'], payload['arguments']
            if name == 'desktop.window.resolve':
                window_id = arguments['description'].removeprefix('window-id:')
                data = {'resolved': True, 'window': copy.deepcopy(next(w for w in self.world['windows'] if w['id'] == window_id))}
            elif name == 'desktop.window.beside': data = await beside(arguments, self.context)
            elif name == 'desktop.world': data = await self.snapshot()
            else: raise AssertionError(name)
            return {'status': 'completed', 'result': data}
        self.planner.request_tool.side_effect = request
        plan = self.planner.try_plan('put window-id:target beside window-id:anchor', 'execution')
        with patch('ev.tools.window_pair.asyncio.sleep', new=AsyncMock()):
            result = await self.planner.execute(plan)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['goal_verified'])
        self.assertEqual(self.planner.last_window_operation['tool'], 'desktop.window.beside')
        correction = self.planner.try_plan('no, I meant the other target', 'correction')
        self.assertEqual(correction.steps[-1].tool, 'desktop.window.beside')
        self.assertEqual(correction.steps[-1].arguments['anchor_id'], 'anchor')
        self.assertEqual(correction.steps[0].arguments['exclude_window_id'], 'target')
