import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from ev.health import GiggleGuard
from ev.events import PhaxEventBus


class HealthTests(unittest.IsolatedAsyncioTestCase):
    def speech_voice(self):
        return SimpleNamespace(
            neural_vad=SimpleNamespace(config={'neural_enabled':False}, process=None, start=AsyncMock()),
            stt=SimpleNamespace(config={'persistent_server':False}, _server_process=None, prewarm=AsyncMock()),
            tts=SimpleNamespace(selected='espeak-ng',
                                piper=SimpleNamespace(config={'persistent_worker':True}, _worker=None),
                                prewarm=AsyncMock()),
        )

    async def test_disabled_and_on_demand_workers_do_not_spend_recovery_budget(self):
        voice = self.speech_voice()
        health = GiggleGuard(SimpleNamespace(voice=voice, bus=PhaxEventBus()))
        health.components = {name: {'state':'READY'} for name in ('VAD','STT','TTS')}
        await health.check_speech_workers()
        self.assertEqual({name: row['state'] for name,row in health.components.items()},
                         {'VAD':'DISABLED','STT':'ON_DEMAND','TTS':'ON_DEMAND'})
        self.assertEqual(dict(health.attempts), {})
        voice.neural_vad.start.assert_not_awaited()
        voice.stt.prewarm.assert_not_awaited()
        voice.tts.prewarm.assert_not_awaited()

    async def test_persistent_worker_defaults_and_selected_engine_are_respected(self):
        voice = self.speech_voice()
        voice.neural_vad.config['neural_enabled'] = True
        voice.stt.config['persistent_server'] = True
        voice.tts.selected = 'piper'
        voice.tts.piper.config = {}
        for owner, field in ((voice.neural_vad, 'process'), (voice.stt, '_server_process'),
                             (voice.tts.piper, '_worker')):
            setattr(owner, field, SimpleNamespace(returncode=None))
        health = GiggleGuard(SimpleNamespace(voice=voice, bus=PhaxEventBus()))
        await health.check_speech_workers()
        self.assertEqual([health.components[n]['state'] for n in ('VAD','STT','TTS')], ['READY']*3)
        self.assertTrue(all(not attempts for attempts in health.attempts.values()))
        voice.tts.prewarm.assert_not_awaited()

    async def test_repeated_failure_stops_after_three_repairs(self):
        h = GiggleGuard(SimpleNamespace(bus=PhaxEventBus()))
        healthy = AsyncMock(return_value=False)
        repair = AsyncMock()
        for now in (100, 131, 162, 193):
            await h.check("worker", healthy, repair, now)
        self.assertEqual(repair.await_count, 3)
        self.assertEqual(h.components["worker"]["state"], "BLOCKED")

    async def test_success_requires_observed_postcondition(self):
        h = GiggleGuard(SimpleNamespace(bus=PhaxEventBus()))
        repair = AsyncMock()
        await h.check("worker", AsyncMock(side_effect=[False, True]), repair, 100)
        self.assertTrue(h.components["worker"]["recovered"])

    async def test_healthy_worker_is_never_restarted(self):
        h = GiggleGuard(SimpleNamespace(bus=PhaxEventBus()))
        repair = AsyncMock()
        await h.check("worker", AsyncMock(return_value=True), repair, 100)
        repair.assert_not_called()

    async def test_hot_model_recovery_defers_without_spending_attempts(self):
        from ev.ai.local_llama import LocalLlamaProvider

        model = LocalLlamaProvider({"prewarm": True}, ownership_path="/unused-owner")
        model._managed_healthy = AsyncMock(return_value=False)
        model.prewarm = AsyncMock()
        h = GiggleGuard(SimpleNamespace(bus=PhaxEventBus(), brain=SimpleNamespace(provider=model)))
        with patch("ev.telemetry.read_temperature", return_value={"celsius": 90}):
            await h.check_local_model()
        self.assertEqual(h.components["Local AI"]["state"], "DEFERRED")
        self.assertEqual(len(h.attempts["Local AI"]), 0)
        model.prewarm.assert_not_called()

    async def test_cooled_model_recovery_verifies_health_after_warming(self):
        from ev.ai.local_llama import LocalLlamaProvider

        model = LocalLlamaProvider({"prewarm": True}, ownership_path="/unused-owner")
        model._managed_healthy = AsyncMock(side_effect=[False, False, True])
        model.prewarm = AsyncMock()
        h = GiggleGuard(SimpleNamespace(bus=PhaxEventBus(), brain=SimpleNamespace(provider=model)))
        with patch("ev.telemetry.read_temperature", return_value={"celsius": 65}):
            await h.check_local_model()
        model.prewarm.assert_awaited_once()
        self.assertTrue(h.components["Local AI"]["recovered"])

    async def test_external_model_is_not_warmed_or_probed(self):
        from ev.ai.local_llama import LocalLlamaProvider

        model = LocalLlamaProvider({"prewarm": True})
        model._managed_healthy = AsyncMock()
        model.prewarm = AsyncMock()
        h = GiggleGuard(SimpleNamespace(bus=PhaxEventBus(), brain=SimpleNamespace(provider=model)))
        await h.check_local_model()
        model._managed_healthy.assert_not_called()
        model.prewarm.assert_not_called()

    async def test_disabled_wake_is_not_restarted_by_supervisor(self):
        import asyncio

        voice = SimpleNamespace(
            wake_desired=False, privacy_mode=False, wake_paused=False, resource_suspended=False
        )
        core = SimpleNamespace(
            bus=PhaxEventBus(),
            voice=voice,
            state=SimpleNamespace(current=SimpleNamespace(value="DORMANT")),
        )
        health = GiggleGuard(core)
        health.check = AsyncMock()
        health.check_local_model = AsyncMock()
        with patch(
            "ev.health.asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError])
        ):
            with self.assertRaises(asyncio.CancelledError):
                await health.run()
        health.check.assert_not_awaited()
        health.check_local_model.assert_awaited_once()

    async def test_hung_probe_does_not_block_other_workers(self):
        import asyncio
        health = GiggleGuard(SimpleNamespace(bus=PhaxEventBus()))
        health.probe_timeout = .02
        async def hung(): await asyncio.Event().wait()
        repair = AsyncMock()
        await asyncio.wait_for(health.check('stuck', hung, repair), .3)
        repair.assert_not_awaited()
        self.assertEqual(health.components['stuck']['state'], 'FAILED')
        await health.check('next', AsyncMock(return_value=True), repair)
        self.assertEqual(health.components['next']['state'], 'READY')

    async def test_model_probe_failure_does_not_kill_supervision(self):
        from ev.ai.local_llama import LocalLlamaProvider
        model = LocalLlamaProvider({'prewarm': True}, ownership_path='/unused-owner')
        model._managed_healthy = AsyncMock(side_effect=OSError('probe failed'))
        model.prewarm = AsyncMock()
        health = GiggleGuard(SimpleNamespace(bus=PhaxEventBus(), brain=SimpleNamespace(provider=model)))
        await health.check_local_model()
        self.assertEqual(health.components['Local AI']['state'], 'FAILED')
        model.prewarm.assert_not_awaited()
        model._managed_healthy = AsyncMock(return_value=True)
        await health.check_local_model()
        self.assertEqual(health.components['Local AI']['state'], 'READY')
