import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from ev.events import PhaxEventBus
from ev.state import StateMachine
from ev.voice.manager import VoiceManager


class SpeechStopMetricTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bus = PhaxEventBus()
        self.manager = VoiceManager({'wake': {'enabled': False}}, self.bus, StateMachine(self.bus))

    def player(self):
        player = Mock(returncode=None)
        async def wait():
            player.returncode = -15
            return -15
        player.wait = AsyncMock(side_effect=wait)
        self.manager.tts_process = player
        self.manager.speaking = True
        return player

    async def test_typed_stop_measures_backend_exit_without_claiming_acoustic_barge_in(self):
        player = self.player()
        result = await self.manager.stop_speaking('user_request', 'owned-stop')
        self.assertEqual(result['latency_scope'], 'accepted_stop_to_playback_process_exit')
        self.assertFalse(result['acoustic_latency_verified'])
        self.assertIsNotNone(self.manager.diagnostics['speech_stop_latency_ms'])
        self.assertIsNone(self.manager.diagnostics['barge_in_latency_ms'])
        self.assertEqual(self.manager.diagnostics['speech_stop_reason'], 'user_request')
        player.terminate.assert_called_once()
        player.wait.assert_awaited_once()
        event = next(e for e in self.bus.history() if e['type'] == 'tts.interrupted')
        self.assertTrue(event['payload']['player_was_running'])
        self.assertFalse(event['payload']['acoustic_latency_verified'])

    async def test_accepted_wake_interruption_has_explicit_backend_scope_and_timestamp(self):
        self.player()
        result = await self.manager.stop_speaking('wake_word_barge_in', 'owned-wake')
        diagnostics = self.manager.diagnostics
        self.assertEqual(diagnostics['barge_in_latency_ms'], result['latency_ms'])
        self.assertEqual(diagnostics['barge_in_latency_scope'], 'accepted_stop_to_playback_process_exit')
        self.assertGreater(diagnostics['barge_in_observed_at'], 0)
        self.assertFalse(result['acoustic_latency_verified'])

    async def test_stop_during_synthesis_only_measures_setting_the_cancel_flag(self):
        self.manager.speech_pending = True
        result = await self.manager.stop_speaking('wake_word_barge_in', 'owned-pending')
        self.assertEqual(result['latency_scope'], 'accepted_stop_to_cancel_flag')
        self.assertIsNone(self.manager.diagnostics['barge_in_latency_ms'])
        self.assertIsNone(self.manager.diagnostics['barge_in_observed_at'])
        self.assertEqual(self.manager.tts_cancel_reason, 'wake_word_barge_in')
        event = next(e for e in self.bus.history() if e['type'] == 'tts.interrupted')
        self.assertFalse(event['payload']['player_was_running'])

    async def test_later_privacy_stop_preserves_the_dated_wake_metric(self):
        self.player()
        await self.manager.stop_speaking('wake_word_barge_in', 'owned-wake')
        earlier = {k: self.manager.diagnostics[k] for k in ('barge_in_latency_ms', 'barge_in_latency_scope', 'barge_in_observed_at')}
        self.player()
        await self.manager.stop_speaking('privacy_mode', 'owned-privacy')
        self.assertEqual(earlier, {k: self.manager.diagnostics[k] for k in earlier})
        self.assertEqual(self.manager.diagnostics['speech_stop_reason'], 'privacy_mode')

    async def test_no_speech_or_live_player_does_not_invent_a_zero_stop_time(self):
        result = await self.manager.stop_speaking('user_request', 'owned-empty')
        self.assertEqual(result['status'], 'not_speaking')
        self.assertIsNone(self.manager.diagnostics['speech_stop_latency_ms'])
        self.assertEqual(self.bus.history(), [])
        player = self.player()
        player.returncode = 0
        result = await self.manager.stop_speaking('wake_word_barge_in', 'owned-exited')
        self.assertEqual(result['latency_scope'], 'accepted_stop_to_cancel_flag')
        self.assertIsNone(self.manager.diagnostics['barge_in_latency_ms'])
        player.terminate.assert_not_called()
        player.wait.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
