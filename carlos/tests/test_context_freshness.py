import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ev.context_age import recent_age
from ev.events import PhaxEventBus
from ev.planner import TaskPlanner
from ev.state import StateMachine
from ev.tools import ToolContext, ToolRegistry
from ev.tools.preferences import context_records


class ContextFreshnessTests(unittest.TestCase):
    def setUp(self):
        bus = PhaxEventBus()
        registry = ToolRegistry(ToolContext({}, bus, logging.getLogger('context')))
        self.planner = TaskPlanner(registry, AsyncMock(), bus, StateMachine(bus))
        self.daily = SimpleNamespace(records=lambda _kind: {})
        self.clock = patch('ev.context_age.time.monotonic', return_value=1000.0)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def remember_window(self, observed_at=999):
        self.planner.last_entities = {'window': {'id': 'owned-native-window',
                                                'app_id': 'ambiguous-app', 'title': 'Shared title'},
                                      'window_at': observed_at}

    def test_follow_up_uses_exact_saved_id_for_common_pronouns(self):
        self.remember_window()
        for phrase in ('it', 'that', 'this', 'the one', 'that one', 'this one', 'that window'):
            with self.subTest(phrase=phrase):
                plan = self.planner.try_plan('center ' + phrase, 'follow-up')
                self.assertEqual(plan.steps[0].arguments['description'], 'window-id:owned-native-window')
        self.assertEqual(self.planner.try_plan('center Firefox', 'explicit').steps[0].arguments['description'], 'Firefox')

    def test_missing_id_never_falls_back_to_title_or_app(self):
        self.remember_window()
        for value in (None, '', '  ', 42, False):
            with self.subTest(value=value):
                self.planner.last_entities['window']['id'] = value
                plan = self.planner.try_plan('center that one', 'missing-id')
                self.assertEqual(plan.steps[0].arguments['description'], 'window-id:missing-context')

    def test_invalid_window_times_refuse_without_crashing_or_guessing(self):
        for value in (None, True, False, '999', float('nan'), float('inf'), -float('inf'),
                      1001, 699.99, 10**1000):
            with self.subTest(value=str(value)[:32]):
                self.remember_window(value)
                plan = self.planner.try_plan('center it', 'expired')
                self.assertEqual(plan.steps[0].arguments['description'], 'window-id:expired-context')
        self.remember_window()
        self.planner.last_entities.pop('window_at')
        self.assertEqual(self.planner.try_plan('center it', 'missing-time').steps[0].arguments['description'],
                         'window-id:expired-context')

    def test_bounded_age_includes_valid_zero_and_limit(self):
        self.assertEqual(recent_age(1000, 300), 0)
        self.assertEqual(recent_age(700, 300), 300)
        self.assertIsNone(recent_age(699.9, 300))
        self.assertIsNone(recent_age(1000.1, 300))
        self.assertIsNone(recent_age(999, 300, now=float('nan')))

    def test_expired_numbered_choice_never_selects_an_item(self):
        for value in (None, True, '999', 1001, 879.9, float('nan'), float('inf'), 10**1000):
            with self.subTest(value=str(value)[:32]):
                self.planner.last_entities['choices'] = {
                    'kind': 'task', 'at': value, 'items': [{'id': 'must-not-complete'}]}
                plan = self.planner.try_plan('complete the first one', 'choice')
                self.assertEqual([step.tool for step in plan.steps], ['interaction.selection_status'])
        self.planner.last_entities['choices']['at'] = 1000
        plan = self.planner.try_plan('complete the first one', 'fresh-choice')
        self.assertEqual(plan.steps[0].arguments, {'identifier': 'must-not-complete'})

    def test_correction_does_not_replay_an_expired_operation(self):
        self.remember_window(1001)
        self.planner.last_window_operation = {'tool': 'desktop.window.layout', 'arguments': {'layout': 'left'}}
        self.assertIsNone(self.planner.try_plan('no I meant the other Firefox', 'future-correction'))
        self.planner.last_entities['window_at'] = 999
        plan = self.planner.try_plan('no I meant the other Firefox', 'fresh-correction')
        self.assertEqual(plan.steps[0].arguments['exclude_window_id'], 'owned-native-window')

    def test_model_hints_share_expiry_and_do_not_publish_bad_references(self):
        for value in (None, True, '999', 1001, 699, float('nan'), float('inf'), 10**1000):
            with self.subTest(value=str(value)[:32]):
                self.remember_window(value)
                hints = context_records('center it', self.daily, self.planner.last_entities)
                self.assertFalse(any(item['id'] == 'recent-window' for item in hints))
        self.remember_window()
        hints = context_records('center it', self.daily, self.planner.last_entities)
        hint = next(item for item in hints if item['id'] == 'recent-window')
        self.assertIn('owned-native-window', hint['content'])
        self.assertIn('NOT necessarily active/existing', hint['content'])
        for value in (None, '', '  ', 42, False):
            self.planner.last_entities['window']['id'] = value
            hints = context_records('center it', self.daily, self.planner.last_entities)
            self.assertFalse(any(item['id'] == 'recent-window' for item in hints))


if __name__ == '__main__':
    unittest.main()
