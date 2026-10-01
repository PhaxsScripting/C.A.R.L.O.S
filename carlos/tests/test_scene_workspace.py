import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from ev.service import CarlosCore
from ev.paths import Paths


class SceneWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.core = CarlosCore(paths=Paths(*(root / name for name in ['config', 'data', 'state', 'cache', 'run'])))
        self.core._request_model_tool = AsyncMock(return_value={
            'status': 'completed', 'result': {
                'plan': {'goal': 'Restore fixture', 'steps': [
                    {'id': 'window0', 'tool': 'workspaces.resolve_window',
                     'arguments': {'name': 'coding', 'window_id': 'saved'}},
                    {'id': 'restore0', 'tool': 'desktop.window.state',
                     'arguments': {'window_id': {'$ref': 'window0.result.window.id'}, 'state': 'restore'}}],
                    'conditions': [{'kind': 'window_state', 'window_id': {'$ref': 'window0.result.window.id'},
                                    'property': 'minimized', 'expected': False}]},
                'gaps': [{'window_id': 'missing', 'reason': 'Unavailable'}],
                'context_checks': [{'kind': 'editor_files', 'restored': False}]}})
        self.core.daily.save('workspace_layout', 'coding', {'windows': []})
        self.save = self.core.tools.get('carlos.scenes.save').executor

    async def asyncTearDown(self):
        self.core.daily.close()
        self.core.task_journal.close()
        self.core.memory.close()
        self.temp.cleanup()

    def save_scene(self, **changes):
        definition = {'name': 'homecoming', 'commands': [], 'hud': 'CARLOS',
                      'quiet': False, 'workspace': 'Coding'} | changes
        return self.save(definition, self.core.tools.context)

    async def test_saved_workspace_is_normalized_and_missing_name_does_not_overwrite_scene(self):
        self.save_scene()
        self.assertEqual(self.core.scenes.definitions()['homecoming']['workspace'], 'coding')
        with self.assertRaisesRegex(ValueError, 'existing saved workspace'):
            self.save_scene(workspace='missing')
        self.assertEqual(self.core.scenes.definitions()['homecoming']['workspace'], 'coding')
        self.core._request_model_tool.assert_not_awaited()

    async def test_workspace_and_commands_share_plan_with_exact_references_and_dependencies(self):
        self.save_scene(commands=['open Firefox'])
        plan, layout = await self.core.scenes.prepare('homecoming', 'fixture')
        self.assertEqual(plan.steps[1].arguments['window_id'], {'$ref': 'window0.result.window.id'})
        command_steps = plan.steps[2:]
        self.assertTrue(command_steps)
        self.assertEqual(command_steps[0].dependencies, ['restore0'])
        identifiers = {step.id for step in plan.steps}
        self.assertEqual(len(identifiers), len(plan.steps))
        for step in command_steps:
            self.assertTrue(step.id.startswith('scene_'))
            self.assertTrue(set(step.dependencies) <= identifiers)
        self.assertEqual(layout['gaps'][0]['window_id'], 'missing')
        self.assertEqual(plan.goal_conditions[0]['window_id'], {'$ref': 'window0.result.window.id'})

    async def test_invalid_command_is_rejected_before_workspace_observation_or_any_execution(self):
        self.save_scene(commands=['do something impossible fixture'])
        with self.assertRaisesRegex(ValueError, 'cannot safely plan'):
            await self.core.scenes.prepare('homecoming', 'fixture')
        self.core._request_model_tool.assert_not_awaited()
        self.assertEqual(self.core.scenes.current['state'], 'IDLE')

    async def test_deleted_workspace_does_not_run_remaining_scene_commands(self):
        self.save_scene(commands=['open Firefox'])
        self.core._request_model_tool.return_value = {'status': 'failed'}
        self.core.planner.execute = AsyncMock()
        result = await self.core._submit_action_clauses_impl('activate homecoming scene', 'fixture')
        self.assertEqual(result['status'], 'failed')
        self.core.planner.execute.assert_not_awaited()
        self.assertEqual(self.core.scenes.current['state'], 'IDLE')

    async def test_stop_during_preparation_does_not_activate_or_execute_scene(self):
        self.save_scene()
        original = self.core._request_model_tool.return_value

        async def stop(*args):
            self.core._action_generation += 1
            return original
        self.core._request_model_tool.side_effect = stop
        self.core.planner.execute = AsyncMock()
        result = await self.core._submit_action_clauses_impl('activate homecoming scene', 'fixture')
        self.assertEqual(result['status'], 'cancelled')
        self.core.planner.execute.assert_not_awaited()
        self.assertEqual(self.core.scenes.current['state'], 'IDLE')

    async def test_execution_failure_and_partial_restoration_are_reported(self):
        self.save_scene()
        self.core.planner.execute = AsyncMock(return_value={'status': 'failed', 'response': 'Fixture failed'})
        result = await self.core._submit_action_clauses_impl('activate homecoming scene', 'fixture')
        self.core.planner.execute.assert_awaited_once()
        self.assertEqual(result['scene']['state'], 'FAILED')
        self.assertEqual(result['workspace_gaps'][0]['window_id'], 'missing')
        self.assertFalse(result['context_checks'][0]['restored'])
        self.assertIn('Some saved items', result['response'])

    async def test_cancellation_leaves_scene_cancelled(self):
        self.save_scene()
        self.core.planner.execute = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.core._submit_action_clauses_impl('activate homecoming scene', 'fixture')
        self.assertEqual(self.core.scenes.current['state'], 'CANCELLED')

    async def test_guest_cannot_observe_or_restore_a_personal_workspace(self):
        self.save_scene()
        self.core.config.setdefault('carlos', {})['privacy_mode'] = 'GUEST'
        result = await self.core._submit_action_clauses_impl('activate homecoming scene', 'fixture')
        self.assertEqual(result['status'], 'denied')
        self.core._request_model_tool.assert_not_awaited()

    async def test_preview_uses_real_dry_run_executor_and_sends_no_window_actions(self):
        self.save_scene(commands=['open Firefox'])
        self.core.scenes.activate('gaming')
        previous = dict(self.core.scenes.current)
        result = await self.core._submit_action_clauses_impl('preview homecoming scene', 'fixture')
        self.assertTrue(result['preview'])
        self.assertEqual(result['plan']['status'], 'DRY_RUN')
        self.assertTrue(result['plan']['dry_run'])
        self.assertTrue(all(step['attempts'] == 0 for step in result['plan']['steps']))
        self.assertEqual(self.core._request_model_tool.await_count, 1)
        self.assertEqual(self.core._request_model_tool.await_args.args[0]['name'], 'workspaces.restore_plan')
        self.assertEqual(self.core.scenes.current, previous)

    async def test_empty_scene_preview_keeps_previous_hud_and_quiet_policy(self):
        self.core.scenes.activate('coding')
        previous = dict(self.core.scenes.current)
        result = await self.core._submit_action_clauses_impl('dry-run activate gaming scene', 'fixture')
        self.assertTrue(result['preview'])
        self.assertTrue(result['scene_preview']['quiet'])
        self.assertEqual(self.core.scenes.current, previous)
        self.core._request_model_tool.assert_not_awaited()

    def test_preview_grammar_rejects_negations_discussion_and_quoted_requests(self):
        for text in ["don't preview coding scene", 'explain preview coding scene',
                     'preview "activate coding scene"', 'preview do not activate coding scene']:
            self.assertIsNone(self.core.scenes.parse_request(text), text)
        self.assertEqual(self.core.scenes.parse_request('preview activate coding scene'), ('coding', True))
