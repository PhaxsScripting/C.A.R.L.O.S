import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from ev.paths import Paths
from ev.service import CarlosCore
from ev.tools.base import ValidationError
from ev.tools.history import historical_query, period_bounds, recall
from ev.tools.project_memory import select_project


class HistoryRecallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = CarlosCore(paths=Paths(*(self.root / name for name in ('config','data','state','cache','run'))))
        self.core.config['security']['allowed_roots'] = [str(self.root)]
        self.core.config['assistant']['speak_responses'] = False
        self.alpha = self.root / 'alpha'; self.alpha.mkdir()
        self.beta = self.root / 'beta'; self.beta.mkdir()
        self.context = self.core.tools.context
        self.clock = datetime(2026,10,1,14,tzinfo=ZoneInfo('America/New_York'))

    def tearDown(self):
        self.core.daily.close(); self.core.task_journal.close(); self.core.memory.close()
        self.temp.cleanup()

    def receipt(self, correlation, content, timestamp, project=''):
        self.core.memory.add_conversation(correlation,'user',content,project=project)
        self.core.memory._connection.execute('UPDATE conversations SET created_at=? WHERE correlation_id=?', (timestamp,correlation))
        self.core.memory._connection.commit()

    async def test_last_night_uses_local_evening_and_morning_without_other_scopes(self):
        self.receipt('evening','alpha_canary','2026-09-30T23:00:00+00:00',str(self.alpha))
        self.receipt('morning','beta_canary','2026-10-01T08:00:00+00:00',str(self.beta))
        self.receipt('day','day_canary','2026-10-01T12:00:00+00:00',str(self.alpha))
        await select_project({'project':str(self.alpha)},self.context)
        with patch('ev.tools.history.period_bounds',return_value=period_bounds('last_night',self.clock)):
            result = await recall({'period':'last_night'},self.context)
        self.assertIn('alpha_canary',str(result))
        self.assertNotIn('beta_canary',str(result))
        self.assertNotIn('day_canary',str(result))
        self.assertFalse(result['live_state_verified'])
        self.assertEqual(result['source'],'saved_conversation_receipts')

    async def test_saved_notes_are_labelled_undated_and_use_captured_project(self):
        self.core.memory.remember_project(str(self.alpha),'HoloHand alpha_note_canary')
        self.core.memory.remember_project(str(self.beta),'HoloHand beta_note_canary')
        await select_project({'project':str(self.beta)},self.context)
        token = self.core.brain.context_project.set(str(self.alpha))
        try:
            result = await recall({'query':'HoloHand'},self.context)
        finally:
            self.core.brain.context_project.reset(token)
        self.assertIn('alpha_note_canary',str(result))
        self.assertNotIn('beta_note_canary',str(result))
        self.assertIn('undated context, not activity evidence',result['message'])
        self.assertEqual(result['entries'],[])
        self.assertFalse(result['live_state_verified'])

    async def test_query_is_literal_and_legacy_credentials_are_redacted_on_read(self):
        self.receipt('literal','HoloHand 100%_ done','2026-09-30T23:00:00+00:00')
        self.receipt('other','HoloHand 100 other','2026-09-30T23:00:00+00:00')
        self.core.memory._connection.execute('UPDATE conversations SET content=? WHERE correlation_id=?', ('HoloHand 100%_ token=historical_canary_secret','literal'))
        self.core.memory._connection.commit()
        with patch('ev.tools.history.period_bounds',return_value=period_bounds('past_week',self.clock)):
            result = await recall({'query':'100%_'},self.context)
            empty = await recall({'query':"' OR 1=1 --"},self.context)
        self.assertEqual(len(result['entries']),1)
        self.assertNotIn('historical_canary_secret',str(result))
        self.assertEqual(empty['entries'],[])
        self.assertIn('cannot tell what happened',empty['message'])

    async def test_large_results_are_bounded_and_explicit_project_must_be_allowed(self):
        for index in range(80):
            self.receipt(str(index),chr(0x1f30c)*8000,'2026-09-30T23:00:00+00:00')
        with patch('ev.tools.history.period_bounds',return_value=period_bounds('past_week',self.clock)):
            result = await recall({'limit':100},self.context)
        self.assertLess(len(json.dumps(result,ensure_ascii=False).encode('utf-8')),360000)
        self.assertTrue(result['has_more'])
        with self.assertRaises(ValidationError):
            await self.core.request_tool({'name':'memory.recall','arguments':{'project':'/usr'}},'fixture')

    async def test_natural_history_request_is_offline_read_only_and_does_not_call_provider(self):
        self.receipt('evening','HoloHand fixture activity','2026-09-30T23:00:00+00:00')
        self.core.brain.provider.begin = AsyncMock(side_effect=AssertionError('No provider needed'))
        with patch('ev.tools.history.period_bounds',return_value=period_bounds('last_night',self.clock)):
            result = await self.core._submit_action_clauses('What was I working on last night?','history-fixture')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['actions_executed'],0)
        self.assertIn('HoloHand fixture activity',result['response'])
        self.core.brain.provider.begin.assert_not_awaited()
        with self.core.task_journal._db() as db:
            receipts = str([tuple(row) for row in db.execute('SELECT detail FROM agent_tasks')])
            receipts += str([tuple(row) for row in db.execute('SELECT receipt FROM agent_steps')])
        self.assertNotIn('HoloHand fixture activity',receipts)
        self.assertTrue(self.core.tools.get('memory.recall').read_only)
        self.assertTrue(self.core.tools.get('memory.recall').offline_available)

    async def test_guest_refused_and_private_read_does_not_persist_new_data(self):
        self.core.config['carlos']['privacy_mode']='GUEST'
        result = await self.core.request_tool({'name':'memory.recall','arguments':{}},'fixture')
        self.assertEqual(result['status'],'denied')
        self.core.config['carlos']['privacy_mode']='NORMAL'
        self.core.memory.set_private(True)
        self.receipt('private','private_history_canary','2026-09-30T23:00:00+00:00')
        with patch('ev.tools.history.period_bounds',return_value=period_bounds('past_week',self.clock)):
            result = await recall({},self.context)
        self.assertEqual(result['storage'],'RAM_ONLY')
        self.assertIn('private_history_canary',str(result))
        self.core.memory.set_private(False)
        self.assertNotIn('private_history_canary',str(self.core.memory.recent_conversation()))

    def test_dst_and_early_morning_boundaries_are_explicit(self):
        spring = datetime(2026,3,8,12,tzinfo=ZoneInfo('America/New_York'))
        start,end=period_bounds('yesterday',spring)
        self.assertEqual(datetime.fromisoformat(end)-datetime.fromisoformat(start),timedelta(hours=24))
        start,end=period_bounds('today',spring)
        self.assertEqual(datetime.fromisoformat(end)-datetime.fromisoformat(start),timedelta(hours=11))
        early=datetime(2026,10,1,2,tzinfo=ZoneInfo('America/New_York'))
        start,end=period_bounds('last_night',early)
        self.assertEqual(end,early.astimezone(UTC).isoformat())
        fall = datetime(2026,11,2,12,tzinfo=ZoneInfo('America/New_York'))
        start,end=period_bounds('yesterday',fall)
        self.assertEqual(datetime.fromisoformat(end)-datetime.fromisoformat(start),timedelta(hours=25))
        with self.assertRaises(ValueError): period_bounds('unknown',spring)
        with self.assertRaises(ValueError): period_bounds('today',datetime(2026,10,1))

    def test_parser_does_not_turn_actions_or_quoted_history_into_recall(self):
        self.assertEqual(historical_query('What happened with HoloHand?'),{'period':'past_week','query':'HoloHand'})
        self.assertEqual(historical_query('What was I doing yesterday?'),{'period':'yesterday'})
        for text in ('type "What happened with HoloHand?"','open what I was doing last night','delete history','What happened with HoloHand? then delete files'):
            self.assertIsNone(historical_query(text))
