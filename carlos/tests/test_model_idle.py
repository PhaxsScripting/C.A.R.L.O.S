import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ev.ai.local_llama import LocalLlamaProvider
from ev.events import PhaxEventBus
from ev.health import GiggleGuard


class ModelIdleTests(unittest.IsolatedAsyncioTestCase):
    def provider(self):
        model = LocalLlamaProvider({'idle_unload_seconds': 60}, ownership_path='/unused-owner')
        model._last_used = 100
        model._process = SimpleNamespace(returncode=None)
        model._owns_process = True
        model._managed_endpoint_owned = Mock(return_value=True)
        return model

    async def test_idle_release_reaps_a_real_owned_child(self):
        model = self.provider()
        child = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(30)')
        model._attach_child(child)
        try:
            self.assertFalse(await model.release_if_idle(159))
            self.assertTrue(await model.release_if_idle(160))
            self.assertIsNotNone(child.returncode)
            self.assertTrue(model.idle_unloaded)
            self.assertEqual(model.lifecycle_status()['state'], 'UNLOADED')
        finally:
            if child.returncode is None:
                child.terminate()
                await child.wait()

    async def test_active_request_and_external_endpoint_are_never_unloaded(self):
        model = self.provider()
        model.close = AsyncMock()
        async with model.model_use():
            self.assertFalse(await model.release_if_idle(model._last_used + 1000))
            self.assertEqual(model.lifecycle_status()['state'], 'HOT')
        self.assertEqual(model._active_requests, 0)
        model._ownership_path = None
        self.assertFalse(await model.release_if_idle(model._last_used + 1000))
        model.close.assert_not_awaited()

    async def test_disabled_invalid_and_unowned_timeouts_cannot_release_a_process(self):
        model = self.provider()
        model.close = AsyncMock()
        for timeout in [0, -1, 'invalid', None, float('inf'), float('nan')]:
            model.config['idle_unload_seconds'] = timeout
            self.assertFalse(await model.release_if_idle(1000))
        model.config['idle_unload_seconds'] = 60
        model._managed_endpoint_owned.return_value = False
        self.assertFalse(await model.release_if_idle(1000))
        model.close.assert_not_awaited()

    async def test_waiting_request_starts_only_after_idle_cleanup_and_resets_policy(self):
        model = self.provider()
        closing, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        child = model._process

        async def close():
            closing.set()
            await release.wait()
            child.returncode = 0
            model._process = None
        model.close = close

        async def request():
            async with model.model_use():
                entered.set()
                self.assertFalse(model.idle_unloaded)
                self.assertEqual(model._active_requests, 1)
        cleanup = asyncio.create_task(model.release_if_idle(200))
        await closing.wait()
        pending = asyncio.create_task(request())
        await asyncio.sleep(0)
        self.assertFalse(entered.is_set())
        release.set()
        self.assertTrue(await cleanup)
        await pending
        self.assertFalse(model.idle_unloaded)

    async def test_cancelled_request_releases_busy_count_and_can_reload(self):
        model = self.provider()
        model.idle_unloaded = True
        model._ensure_server = AsyncMock()
        started = asyncio.Event()

        async def hung(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        model._post_unchecked = hung
        task = asyncio.create_task(model._post([], []))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(model._active_requests, 0)
        self.assertFalse(model.idle_unloaded)
        model._post_unchecked = AsyncMock(return_value={'reply': 'loaded'})
        self.assertEqual(await model._post([], []), {'reply': 'loaded'})
        self.assertEqual(model._ensure_server.await_count, 2)

    async def test_supervisor_does_not_restart_intentionally_unloaded_model(self):
        model = self.provider()
        model._process = None
        model.idle_unloaded = True
        model._managed_healthy = AsyncMock()
        model.prewarm = AsyncMock()
        guard = GiggleGuard(SimpleNamespace(bus=PhaxEventBus(), brain=SimpleNamespace(provider=model)))
        await guard.check_local_model()
        self.assertEqual(guard.components['Local AI']['state'], 'ON_DEMAND')
        model._managed_healthy.assert_not_awaited()
        model.prewarm.assert_not_awaited()
        self.assertEqual(len(guard.attempts['Local AI']), 0)
