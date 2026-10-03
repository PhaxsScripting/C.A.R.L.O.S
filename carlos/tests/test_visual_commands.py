import unittest
from unittest.mock import Mock

from ev.commands import direct_action
from ev.planner import TaskPlanner


class VisualCommandTests(unittest.TestCase):
    def test_preview_command_preserves_exact_literal_and_scope(self):
        action = direct_action('show visual targets for "Retry in another app" in Firefox')
        self.assertEqual(action.tool, 'vision.candidates')
        self.assertEqual(action.arguments, {'text':'Retry in another app','description':'Firefox'})
        self.assertEqual(direct_action('find visual candidates for "Retry"').arguments['description'], 'current window')

    def test_explicit_click_transition_uses_finite_native_predicates(self):
        for phrase, kind, prop, expected in (
            ('close','window_absent',None,None),
            ('be minimized','window_state','minimized',True),
            ('be maximized','window_state','maximized',True),
            ('be fullscreen','window_state','fullscreen',True),
            ('exit fullscreen','window_state','fullscreen',False),
        ):
            with self.subTest(phrase=phrase):
                action = direct_action(f'visually click "Retry" in Firefox expecting window to {phrase}')
                self.assertEqual(action.tool, 'vision.click_text')
                condition = action.arguments['expected'][0]
                self.assertEqual(condition['kind'],kind)
                if prop:
                    self.assertEqual(condition['property'],prop)
                    self.assertIs(condition['expected'],expected)

    def test_ordinal_is_explicit_bounded_and_case_insensitive(self):
        for ordinal, number in (('first',1),('SECOND',2),('tenth',10),('20',20)):
            action = direct_action(f'visually click the {ordinal} "Close" in Firefox expecting window to close')
            self.assertEqual(action.arguments['ordinal'],number)
        for ordinal in ('0','21','100','-1','twentieth'):
            self.assertIsNone(direct_action(f'visually click the {ordinal} "Close" expecting window to close'))

    def test_quotes_are_literal_including_action_words(self):
        for quoted in ('"Exit fullscreen and close Firefox"', "'Exit fullscreen and close Firefox'", '“Exit fullscreen and close Firefox”'):
            action = direct_action(f'visually click {quoted} expecting window to exit fullscreen')
            self.assertEqual(action.arguments['text'],'Exit fullscreen and close Firefox')
            self.assertEqual(len(action.arguments['expected']),1)

    def test_explanation_negation_hypothetical_and_missing_result_never_click(self):
        request = 'visually click "Close" in Firefox expecting window to close'
        for text in ('do not '+request, "don't "+request, 'explain how to '+request,
                     'how do I '+request, 'if you '+request, 'imagine '+request,
                     'visually click "Close" in Firefox',
                     'visually click "Close" in Firefox expecting something good',
                     request+' and then open Spotify'):
            with self.subTest(text=text):
                self.assertIsNone(direct_action(text))

    def test_plan_binds_same_native_window_to_click_and_result(self):
        registry = Mock()
        registry.get.return_value.permission.value = 'HIGH'
        planner = TaskPlanner(registry, Mock(), Mock(), Mock())
        plan = planner.try_plan('visually click "Retry" in Firefox expecting window to be fullscreen','fixture')
        self.assertEqual([step.tool for step in plan.steps],['desktop.window.resolve','vision.click_text'])
        self.assertEqual(plan.steps[0].arguments, {'description':'Firefox'})
        args=plan.steps[1].arguments
        self.assertEqual(args['window_id'],args['expected'][0]['window_id'])
        self.assertEqual(plan.steps[1].dependencies,['window'])
        self.assertNotIn('candidate_id',args)

    def test_preview_plan_cannot_send_input_and_dry_run_stays_explicit(self):
        registry = Mock()
        registry.get.return_value.permission.value = 'SENSITIVE'
        planner = TaskPlanner(registry,Mock(),Mock(),Mock())
        plan=planner.try_plan('show visual targets for "Retry" in Firefox','fixture')
        self.assertEqual([step.tool for step in plan.steps],['desktop.window.resolve','vision.candidates'])
        dry=planner.try_plan('preview visually click "Retry" in Firefox expecting window to be fullscreen','fixture')
        self.assertTrue(dry.dry_run)
