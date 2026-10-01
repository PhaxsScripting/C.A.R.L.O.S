import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from ev.paths import Paths
from ev.service import CarlosCore
from ev.planner import preview_requested


class PreviewBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.core = CarlosCore(paths=Paths(*(root / name for name in
                                            ('config', 'data', 'state', 'cache', 'run'))))
        self.core.brain.submit = AsyncMock(side_effect=AssertionError('Preview reached ordinary reasoning'))
        self.core.planner.requester = AsyncMock(side_effect=AssertionError('Preview executed an action'))

    def tearDown(self):
        self.core.daily.close()
        self.core.task_journal.close()
        self.core.memory.close()
        self.temp.cleanup()

    async def test_unsupported_preview_never_calls_reasoning_and_cannot_be_resumed(self):
        for text in ('preview arrange all my projects intelligently',
                     'dry-run arrange all my projects intelligently',
                     'just show me how you would rearrange my whole desktop'):
            result = await self.core._run_journaled_request(text, None)
            self.assertEqual(result['status'], 'preview_unavailable')
            self.assertEqual(result['actions_executed'], 0)
            self.assertIsNone(self.core.task_journal.latest_resumable())
        self.core.brain.submit.assert_not_awaited()
        self.core.planner.requester.assert_not_awaited()

    async def test_prefix_variants_use_dry_executor_without_action_attempts(self):
        for text in ('preview open Firefox', 'please preview open Firefox', 'dry-run open Firefox'):
            result = await self.core._run_journaled_request(text, None)
            self.assertEqual(result['plan']['status'], 'DRY_RUN', result)
            self.assertTrue(all(step['attempts'] == 0 for step in result['plan']['steps']))
        self.core.planner.requester.assert_not_awaited()
        self.core.brain.submit.assert_not_awaited()

    def test_literal_preview_words_do_not_disable_real_typing(self):
        self.assertFalse(preview_requested('type "preview dry run" in Firefox'))
        self.assertFalse(preview_requested('open Preview'))
        plan = self.core.planner.try_plan('type "preview dry run" in Firefox', 'fixture')
        self.assertIsNotNone(plan)
        self.assertFalse(plan.dry_run)
