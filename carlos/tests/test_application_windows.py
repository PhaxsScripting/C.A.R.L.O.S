import asyncio
import copy
import logging
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ev.events import PhaxEventBus
from ev.goals import validate_conditions, verify_conditions
from ev.planner import TaskPlanner
from ev.state import StateMachine
from ev.tools import ToolContext, ToolRegistry, register_builtin_tools
from ev.tools.application_windows import ensure_window, register_application_window_tools
from ev.tools.base import ValidationError
from ev.tools.results import evaluate_result


def window(identifier='owned', app='org.phax.Owned', **fields):
    return {'id': identifier, 'app_id': app, 'resource_class': '', 'normal': True,
            'title': 'Unrelated title', 'output': 'HDMI-A-1', **fields}


def world(windows):
    return {'windows': windows, 'captured_at_monotonic': time.monotonic()}


class ApplicationWindowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entries = {'org.phax.Owned': {'name': 'Owned', '_startup_wm_class': 'owned-class'}}
        self.desktop = SimpleNamespace(snapshot=AsyncMock(return_value=world([window()])))
        self.context = ToolContext({}, PhaxEventBus(), logging.getLogger('test'), desktop=self.desktop)
        self.args = {'desktop_id': 'org.phax.Owned', 'timeout_seconds': 1}
        self.entry_patch = patch('ev.tools.application_windows.desktop_entries', return_value=self.entries)
        self.entry_patch.start()
        self.addCleanup(self.entry_patch.stop)
        self.launch = patch('ev.tools.application_windows.open_application', return_value={'activation_status': 'accepted'}).start()
        self.addCleanup(patch.stopall)

    async def test_exact_app_identity_reuses_window_without_activation_or_launch(self):
        result = await ensure_window(self.args, self.context)
        self.assertTrue(result['verified'])
        self.assertTrue(result['reused'])
        self.assertFalse(result['launch_requested'])
        self.assertEqual(result['window']['id'], 'owned')
        self.launch.assert_not_called()
        self.assertEqual(evaluate_result('applications.ensure_window', result).scope, 'native_window_presence')

    async def test_unique_startup_class_is_usable_without_title_guessing(self):
        self.desktop.snapshot.return_value = world([window(app='unknown-native-id', resource_class='OWNED-CLASS')])
        self.assertTrue((await ensure_window(self.args, self.context))['reused'])
        self.launch.assert_not_called()

    async def test_known_other_app_identity_overrides_a_shared_class(self):
        self.entries['org.phax.Other'] = {'_startup_wm_class': 'owned-class'}
        self.desktop.snapshot.side_effect = [world([window(app='org.phax.Other', resource_class='owned-class')]),
                                            world([window('new')])]
        result = await ensure_window(self.args, self.context)
        self.assertFalse(result['reused'])
        self.assertEqual(result['window']['id'], 'new')
        self.launch.assert_called_once()

    async def test_multiple_windows_and_shared_class_refuse_before_launch(self):
        for windows in ([window(), window('second')], [window(app='', resource_class='owned-class')]):
            self.entries['other-launcher'] = {'_startup_wm_class': 'owned-class'}
            self.desktop.snapshot.return_value = world(windows)
            with self.assertRaisesRegex(ValidationError, 'Multiple|Ambiguous'):
                await ensure_window(self.args, self.context)
        self.launch.assert_not_called()

    async def test_stale_truncated_missing_inventory_and_unknown_app_never_launch(self):
        for data in ({}, {'windows': []}, {**world([]), 'windows_truncated': True},
                     {'windows': [], 'captured_at_monotonic': time.monotonic() - 5}):
            self.desktop.snapshot.return_value = data
            with self.assertRaises(ValidationError):
                await ensure_window(self.args, self.context)
        with self.assertRaises(ValidationError):
            await ensure_window({'desktop_id': 'missing'}, self.context)
        self.launch.assert_not_called()

    async def test_one_launch_waits_for_exact_new_window_even_when_acknowledgement_is_uncertain(self):
        self.launch.return_value = {'activation_status': 'unverified'}
        self.desktop.snapshot.side_effect = [world([window('unrelated', app='other')]), world([]), world([window('fresh')])]
        result = await ensure_window(self.args, self.context)
        self.assertTrue(result['verified'])
        self.assertEqual(result['activation_status'], 'unverified')
        self.assertEqual(result['window']['id'], 'fresh')
        self.assertFalse(result['replay_allowed'])
        self.launch.assert_called_once()

    async def test_failed_launch_and_old_unidentifiable_window_do_not_verify_or_retry(self):
        self.launch.return_value = {'activation_status': 'failed'}
        self.desktop.snapshot.side_effect = [world([window(app='other')]), world([window()])]
        result = await ensure_window(self.args, self.context)
        self.assertFalse(result['verified'])
        self.assertIn('may still open', result['error'])
        self.assertFalse(evaluate_result('applications.ensure_window', result).ok)
        self.launch.assert_called_once()

    async def test_acknowledged_launch_without_window_times_out_without_replay(self):
        self.desktop.snapshot.side_effect = lambda **_args: world([])
        result = await ensure_window(self.args, self.context)
        self.assertFalse(result['verified'])
        self.assertEqual(result['activation_status'], 'accepted')
        self.assertFalse(result['replay_allowed'])
        self.launch.assert_called_once()

    async def test_concurrent_requests_launch_once_and_reuse_the_result(self):
        registry = ToolRegistry(self.context)
        register_application_window_tools(registry)
        visible = []
        async def snapshot(**_args):
            return world(copy.deepcopy(visible))
        self.desktop.snapshot.side_effect = snapshot
        def launch(*_args):
            visible.append(window())
            return {'activation_status': 'accepted'}
        self.launch.side_effect = launch
        executor = registry.get('applications.ensure_window').executor
        results = await asyncio.gather(executor(self.args, self.context), executor(self.args, self.context))
        self.assertEqual(sorted(result['reused'] for result in results), [False, True])
        self.launch.assert_called_once()

    async def test_repeated_cancellation_joins_already_dispatched_launcher(self):
        self.desktop.snapshot.return_value = world([])
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def launch(*_args):
            started.set()
            release.wait(2)
            finished.set()
            return {'activation_status': 'accepted'}
        self.launch.side_effect = launch
        task = asyncio.create_task(ensure_window(self.args, self.context))
        await asyncio.to_thread(started.wait, 1)
        self.assertTrue(started.is_set())
        task.cancel()
        await asyncio.sleep(.01)
        task.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())
        self.launch.assert_called_once()

    async def test_monitor_plan_resolves_before_launch_focuses_and_checks_final_goal(self):
        registry = ToolRegistry(self.context)
        register_builtin_tools(registry)
        spec = registry.get('applications.ensure_window')
        self.assertEqual(spec.public()['contract_gaps'], [])
        self.assertFalse(spec.read_only)
        planner = TaskPlanner(registry, AsyncMock(), self.context.bus, StateMachine(self.context.bus))
        plan = planner.try_plan('open Owned on my big monitor', 'owned')
        self.assertEqual([step.tool for step in plan.steps], ['desktop.output.resolve', 'applications.list',
                         'applications.ensure_window', 'desktop.window.move_to_output', 'desktop.window.activate'])
        self.assertEqual(plan.goal_source, 'trusted_intent')
        self.assertEqual([goal['kind'] for goal in plan.goal_conditions], ['window_output', 'window_active'])
        self.assertFalse(planner._retryable(plan.steps[2]))
        dry = planner.try_plan('dry run, open Owned on my big monitor', 'dry')
        result = await planner.execute(dry)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(dry.dry_run)
        self.assertTrue(all(step.attempts == 0 for step in dry.steps))
        planner.request_tool.assert_not_awaited()

    async def test_final_output_predicate_requires_fresh_exact_enabled_connector_and_focus(self):
        conditions = [{'kind': 'window_output', 'window_id': 'owned', 'expected': 'HDMI-A-1'},
                      {'kind': 'window_active', 'window_id': 'owned'}]
        for expected in ('', True, {}, 'x' * 201):
            with self.assertRaises(ValidationError):
                validate_conditions([{**conditions[0], 'expected': expected}])
        data = {**world([window()]), 'outputs': [{'name': 'HDMI-A-1', 'enabled': True}], 'active_window_id': 'owned'}
        observe = AsyncMock(return_value={'status': 'completed', 'result': data})
        self.assertTrue((await verify_conditions(conditions, observe, 'owned'))['verified'])
        observe.assert_awaited_once()
        for changed in ({'outputs': []}, {'outputs': [{'name': 'HDMI-A-1', 'enabled': False}]},
                        {'outputs_truncated': True}, {'active_window_id': 'other'}, {'windows': [window(output='eDP-1')]}):
            observe.return_value = {'status': 'completed', 'result': {**data, **changed}}
            self.assertFalse((await verify_conditions(conditions, observe, 'owned'))['verified'])


if __name__ == '__main__':
    unittest.main()
